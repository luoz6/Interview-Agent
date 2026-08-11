from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
import re
from typing import Protocol, runtime_checkable

from contracts.evidence.digest import canonical_sha256


SAFE_POSTGRES_SCOPE_PREFIX = re.compile(r"^test_[a-z0-9_]{1,9}_[0-9a-f]{12}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class PostgresScopeError(RuntimeError):
    code = "POSTGRES_SCOPE_ERROR"


class PostgresApprovalInvalid(PostgresScopeError):
    code = "POSTGRES_APPROVAL_INVALID"


class PostgresTargetMismatch(PostgresScopeError):
    code = "POSTGRES_TARGET_MISMATCH"


class PostgresPermissionDenied(PostgresScopeError):
    code = "POSTGRES_PERMISSION_DENIED"


class PostgresScopeNotEmpty(PostgresScopeError):
    code = "POSTGRES_SCOPE_NOT_EMPTY"


class PostgresOwnershipLost(PostgresScopeError):
    code = "POSTGRES_OWNERSHIP_LOST"


class PostgresCleanupResidue(PostgresScopeError):
    code = "POSTGRES_CLEANUP_RESIDUE"


@dataclass(frozen=True)
class PostgresTargetIdentity:
    system_identifier: str
    database_name: str
    database_oid: int
    server_version_num: int
    server_address: str
    server_port: int
    current_user: str
    current_schema: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "system_identifier": self.system_identifier,
            "database_name": self.database_name,
            "database_oid": self.database_oid,
            "server_version_num": self.server_version_num,
            "server_address": self.server_address,
            "server_port": self.server_port,
            "current_user": self.current_user,
            "current_schema": self.current_schema,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_sha256(self.as_dict())


@dataclass(frozen=True)
class PostgresScopeApproval:
    approval_id: str
    approval_receipt_sha256: str
    approved_target_fingerprint: str
    database_allowlist: frozenset[str]
    scope_prefix: str
    expires_at: datetime
    lease_seconds: int = 300

    def validate_static(self, now: datetime) -> None:
        if not self.approval_id.strip():
            raise PostgresApprovalInvalid("approval id is required")
        if SHA256_HEX.fullmatch(self.approval_receipt_sha256) is None:
            raise PostgresApprovalInvalid("approval receipt digest is invalid")
        if SHA256_HEX.fullmatch(self.approved_target_fingerprint) is None:
            raise PostgresApprovalInvalid("approved target fingerprint is invalid")
        if not self.database_allowlist or any(
            not name.strip() for name in self.database_allowlist
        ):
            raise PostgresApprovalInvalid("database allowlist is empty or invalid")
        if SAFE_POSTGRES_SCOPE_PREFIX.fullmatch(self.scope_prefix) is None:
            raise PostgresApprovalInvalid("scope prefix is not an isolated test prefix")
        if self.expires_at.tzinfo is None or self.expires_at <= now:
            raise PostgresApprovalInvalid("approval is expired or timezone-naive")
        if self.lease_seconds < 1 or self.lease_seconds > 3600:
            raise PostgresApprovalInvalid("lease seconds must be between 1 and 3600")


@dataclass(frozen=True)
class PostgresOwnershipMarker:
    approval_receipt_sha256: str
    target_fingerprint: str
    ownership_token: str
    fencing_version: int
    lease_expires_at: datetime


@dataclass
class OwnedPostgresLease:
    scope_prefix: str
    target_identity: PostgresTargetIdentity
    approval_receipt_sha256: str
    ownership_token: str
    fencing_version: int
    lease_expires_at: datetime
    cleanup_receipt: "PostgresCleanupReceipt | None" = None


@dataclass(frozen=True)
class PostgresCleanupReceipt:
    schema_version: str
    approval_id: str
    approval_receipt_sha256: str
    target_fingerprint: str
    scope_prefix: str
    ownership_verified: bool
    target_verified: bool
    resources_examined: int
    resources_removed: int
    residue_count: int
    cleanup_started_at: datetime
    cleanup_finished_at: datetime
    receipt_sha256: str


@runtime_checkable
class OwnedPostgresScopePort(Protocol):
    def open(
        self,
        approval: PostgresScopeApproval,
    ) -> AbstractContextManager[OwnedPostgresLease]:
        ...

    def assert_owned(self, lease: OwnedPostgresLease) -> None:
        ...

    def heartbeat(self, lease: OwnedPostgresLease, *, lease_seconds: int) -> None:
        ...
