from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from app.adapters.postgres.owned_scope import CleanupOutcome, OwnedPostgresScope
from app.ports.postgres_scope import (
    OwnedPostgresScopePort,
    PostgresApprovalInvalid,
    PostgresOwnershipLost,
    PostgresScopeApproval,
    PostgresScopeNotEmpty,
    PostgresTargetIdentity,
    PostgresTargetMismatch,
)


NOW = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)
PREFIX = "test_owned_0123456789ab"


def _identity(database_name: str = "interview_test") -> PostgresTargetIdentity:
    return PostgresTargetIdentity(
        system_identifier="7612345678901234567",
        database_name=database_name,
        database_oid=16384,
        server_version_num=160004,
        server_address="127.0.0.1",
        server_port=5432,
        current_user="interview_test_owner",
        current_schema="public",
    )


def _approval(identity: PostgresTargetIdentity | None = None, **overrides):
    target = identity or _identity()
    values = {
        "approval_id": "approval-20260810-001",
        "approval_receipt_sha256": "a" * 64,
        "approved_target_fingerprint": target.fingerprint,
        "database_allowlist": frozenset({target.database_name}),
        "scope_prefix": PREFIX,
        "expires_at": NOW + timedelta(hours=1),
        "lease_seconds": 300,
    }
    values.update(overrides)
    return PostgresScopeApproval(**values)


class FakeBackend:
    def __init__(self, identity: PostgresTargetIdentity | None = None):
        self.identity = identity or _identity()
        self.markers = {}
        self.relations: set[str] = set()
        self.cleanup_calls = 0

    def inspect_identity(self):
        return self.identity

    def create_scope(self, prefix, marker):
        if any(name.startswith(f"{prefix}_") for name in self.relations):
            raise PostgresScopeNotEmpty("scope is not empty")
        self.markers[prefix] = marker
        self.relations.add(f"{prefix}_ownership")

    @staticmethod
    def _same_owner(left, right):
        return (
            left.approval_receipt_sha256 == right.approval_receipt_sha256
            and left.target_fingerprint == right.target_fingerprint
            and left.ownership_token == right.ownership_token
            and left.fencing_version == right.fencing_version
        )

    def assert_owned(self, prefix, marker, *, now):
        current = self.markers.get(prefix)
        if (
            current is None
            or not self._same_owner(current, marker)
            or current.lease_expires_at <= now
        ):
            raise PostgresOwnershipLost("ownership is not current")

    def heartbeat(self, prefix, marker, *, lease_expires_at, now):
        try:
            self.assert_owned(prefix, marker, now=now)
        except PostgresOwnershipLost:
            return False
        self.markers[prefix] = type(marker)(
            approval_receipt_sha256=marker.approval_receipt_sha256,
            target_fingerprint=marker.target_fingerprint,
            ownership_token=marker.ownership_token,
            fencing_version=marker.fencing_version,
            lease_expires_at=lease_expires_at,
        )
        return True

    def cleanup_owned(self, prefix, marker):
        self.cleanup_calls += 1
        target_verified = self.identity.fingerprint == marker.target_fingerprint
        current = self.markers.get(prefix)
        ownership_verified = current is not None and self._same_owner(current, marker)
        relations = {
            name for name in self.relations if name.startswith(f"{prefix}_")
        }
        if not target_verified or not ownership_verified:
            return CleanupOutcome(
                ownership_verified=ownership_verified,
                target_verified=target_verified,
                resources_examined=0,
                resources_removed=0,
                residue_count=len(relations),
            )
        self.relations.difference_update(relations)
        del self.markers[prefix]
        return CleanupOutcome(
            ownership_verified=True,
            target_verified=True,
            resources_examined=len(relations),
            resources_removed=len(relations),
            residue_count=0,
        )


def _scope(backend: FakeBackend):
    return OwnedPostgresScope(backend, clock=lambda: NOW)


def test_owned_scope_creates_marker_and_emits_cleanup_receipt():
    backend = FakeBackend()
    scope = _scope(backend)

    with scope.open(_approval()) as lease:
        assert isinstance(scope, OwnedPostgresScopePort)
        assert lease.scope_prefix == PREFIX
        assert lease.target_identity.fingerprint == _identity().fingerprint
        assert len(lease.ownership_token) == 32
        backend.relations.add(f"{PREFIX}_sessions")
        backend.relations.add(f"{PREFIX}_runtime_outbox")
        scope.assert_owned(lease)

    assert backend.relations == set()
    assert lease.cleanup_receipt is not None
    assert lease.cleanup_receipt.ownership_verified is True
    assert lease.cleanup_receipt.target_verified is True
    assert lease.cleanup_receipt.resources_examined == 3
    assert lease.cleanup_receipt.resources_removed == 3
    assert lease.cleanup_receipt.residue_count == 0
    rendered = str(asdict(lease.cleanup_receipt)).casefold()
    assert lease.ownership_token not in rendered
    assert "postgresql://" not in rendered


def test_approval_expiry_fails_before_target_inspection():
    backend = FakeBackend()
    approval = _approval(expires_at=NOW)

    with pytest.raises(PostgresApprovalInvalid, match="expired"):
        with _scope(backend).open(approval):
            pytest.fail("expired approval cannot yield a scope")

    assert backend.markers == {}


def test_target_fingerprint_and_database_allowlist_fail_before_marker():
    backend = FakeBackend()
    scope = _scope(backend)

    with pytest.raises(PostgresTargetMismatch, match="identity mismatch"):
        with scope.open(
            _approval(approved_target_fingerprint="f" * 64)
        ):
            pytest.fail("mismatched target cannot yield a scope")
    with pytest.raises(PostgresTargetMismatch, match="allowlist"):
        with scope.open(
            _approval(database_allowlist=frozenset({"different_test"}))
        ):
            pytest.fail("disallowed database cannot yield a scope")

    assert backend.markers == {}


def test_nonempty_scope_is_never_adopted_or_cleaned():
    backend = FakeBackend()
    backend.relations.add(f"{PREFIX}_preexisting")

    with pytest.raises(PostgresScopeNotEmpty):
        with _scope(backend).open(_approval()):
            pytest.fail("nonempty scope cannot be adopted")

    assert backend.relations == {f"{PREFIX}_preexisting"}
    assert backend.cleanup_calls == 0


def test_body_failure_still_cleans_owned_scope():
    backend = FakeBackend()

    with pytest.raises(RuntimeError, match="body failed"):
        with _scope(backend).open(_approval()):
            backend.relations.add(f"{PREFIX}_sessions")
            raise RuntimeError("body failed")

    assert backend.relations == set()
    assert backend.cleanup_calls == 1


def test_cleanup_refuses_replaced_ownership_marker():
    backend = FakeBackend()

    with pytest.raises(PostgresOwnershipLost):
        with _scope(backend).open(_approval()) as lease:
            marker = backend.markers[PREFIX]
            backend.markers[PREFIX] = type(marker)(
                approval_receipt_sha256=marker.approval_receipt_sha256,
                target_fingerprint=marker.target_fingerprint,
                ownership_token="f" * 32,
                fencing_version=marker.fencing_version + 1,
                lease_expires_at=marker.lease_expires_at,
            )
            backend.relations.add(f"{PREFIX}_sessions")
            assert lease.cleanup_receipt is None

    assert f"{PREFIX}_sessions" in backend.relations


def test_cleanup_refuses_changed_physical_target():
    backend = FakeBackend()

    with pytest.raises(PostgresTargetMismatch):
        with _scope(backend).open(_approval()):
            backend.relations.add(f"{PREFIX}_sessions")
            backend.identity = _identity(database_name="other_test")

    assert f"{PREFIX}_sessions" in backend.relations


def test_heartbeat_extends_current_lease_and_rejects_stale_owner():
    backend = FakeBackend()
    scope = _scope(backend)

    with scope.open(_approval()) as lease:
        original_expiry = lease.lease_expires_at
        scope.heartbeat(lease, lease_seconds=600)
        assert lease.lease_expires_at > original_expiry
        backend.markers[PREFIX] = type(backend.markers[PREFIX])(
            approval_receipt_sha256=lease.approval_receipt_sha256,
            target_fingerprint=lease.target_identity.fingerprint,
            ownership_token="e" * 32,
            fencing_version=2,
            lease_expires_at=lease.lease_expires_at,
        )
        with pytest.raises(PostgresOwnershipLost):
            scope.heartbeat(lease, lease_seconds=600)
        backend.markers[PREFIX] = type(backend.markers[PREFIX])(
            approval_receipt_sha256=lease.approval_receipt_sha256,
            target_fingerprint=lease.target_identity.fingerprint,
            ownership_token=lease.ownership_token,
            fencing_version=lease.fencing_version,
            lease_expires_at=lease.lease_expires_at,
        )
