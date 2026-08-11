from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.ports.postgres_scope import OwnedPostgresLease


class MigrationHarnessError(RuntimeError):
    code = "MIGRATION_HARNESS_ERROR"


class MigrationIdempotencyFailure(MigrationHarnessError):
    code = "MIGRATION_IDEMPOTENCY_FAILURE"


@dataclass(frozen=True)
class MigrationExecution:
    migration_id: str
    checksum: str
    applied: bool


@dataclass(frozen=True)
class MigrationHarnessResult:
    migration_id: str
    checksum: str
    first_apply_changed_schema: bool
    second_apply_changed_schema: bool
    validation_passed: bool


@runtime_checkable
class PostgresMigrationAdapterPort(Protocol):
    def apply(self, lease: OwnedPostgresLease) -> MigrationExecution:
        ...

    def validate(self, lease: OwnedPostgresLease) -> None:
        ...


@runtime_checkable
class PostgresMigrationHarnessPort(Protocol):
    def apply_and_validate(
        self,
        lease: OwnedPostgresLease,
    ) -> MigrationHarnessResult:
        ...

    def validate_only(self, lease: OwnedPostgresLease) -> None:
        ...
