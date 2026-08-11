from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
from typing import Callable, Iterator, Protocol
from uuid import UUID, uuid4

from app.ports.postgres_scope import (
    OwnedPostgresLease,
    PostgresCleanupReceipt,
    PostgresCleanupResidue,
    PostgresOwnershipLost,
    PostgresOwnershipMarker,
    PostgresPermissionDenied,
    PostgresScopeApproval,
    PostgresScopeNotEmpty,
    PostgresTargetIdentity,
    PostgresTargetMismatch,
)
from app.services.postgres_connections import ConnectionProvider
from contracts.evidence.digest import canonical_sha256


@dataclass(frozen=True)
class CleanupOutcome:
    ownership_verified: bool
    target_verified: bool
    resources_examined: int
    resources_removed: int
    residue_count: int


class OwnedScopeBackend(Protocol):
    def inspect_identity(self) -> PostgresTargetIdentity:
        ...

    def create_scope(
        self,
        prefix: str,
        marker: PostgresOwnershipMarker,
    ) -> None:
        ...

    def assert_owned(
        self,
        prefix: str,
        marker: PostgresOwnershipMarker,
        *,
        now: datetime,
    ) -> None:
        ...

    def heartbeat(
        self,
        prefix: str,
        marker: PostgresOwnershipMarker,
        *,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool:
        ...

    def cleanup_owned(
        self,
        prefix: str,
        marker: PostgresOwnershipMarker,
    ) -> CleanupOutcome:
        ...


class OwnedPostgresScope:
    def __init__(
        self,
        backend: OwnedScopeBackend,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._backend = backend
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @contextmanager
    def open(
        self,
        approval: PostgresScopeApproval,
    ) -> Iterator[OwnedPostgresLease]:
        started_at = self._clock()
        approval.validate_static(started_at)
        identity = self._backend.inspect_identity()
        if identity.database_name not in approval.database_allowlist:
            raise PostgresTargetMismatch("database is outside the approved allowlist")
        if not hmac.compare_digest(
            identity.fingerprint,
            approval.approved_target_fingerprint,
        ):
            raise PostgresTargetMismatch("physical PostgreSQL target identity mismatch")

        ownership_token = uuid4().hex
        marker = PostgresOwnershipMarker(
            approval_receipt_sha256=approval.approval_receipt_sha256,
            target_fingerprint=identity.fingerprint,
            ownership_token=ownership_token,
            fencing_version=1,
            lease_expires_at=started_at + timedelta(seconds=approval.lease_seconds),
        )
        self._backend.create_scope(approval.scope_prefix, marker)
        lease = OwnedPostgresLease(
            scope_prefix=approval.scope_prefix,
            target_identity=identity,
            approval_receipt_sha256=approval.approval_receipt_sha256,
            ownership_token=ownership_token,
            fencing_version=marker.fencing_version,
            lease_expires_at=marker.lease_expires_at,
        )
        try:
            yield lease
        finally:
            cleanup_started_at = self._clock()
            outcome = self._backend.cleanup_owned(approval.scope_prefix, marker)
            cleanup_finished_at = self._clock()
            receipt_payload = {
                "schema_version": "postgres-cleanup-receipt-v1",
                "approval_id": approval.approval_id,
                "approval_receipt_sha256": approval.approval_receipt_sha256,
                "target_fingerprint": identity.fingerprint,
                "scope_prefix": approval.scope_prefix,
                "ownership_verified": outcome.ownership_verified,
                "target_verified": outcome.target_verified,
                "resources_examined": outcome.resources_examined,
                "resources_removed": outcome.resources_removed,
                "residue_count": outcome.residue_count,
                "cleanup_started_at": cleanup_started_at.isoformat(),
                "cleanup_finished_at": cleanup_finished_at.isoformat(),
            }
            lease.cleanup_receipt = PostgresCleanupReceipt(
                **{
                    key: value
                    for key, value in receipt_payload.items()
                    if key not in {"cleanup_started_at", "cleanup_finished_at"}
                },
                cleanup_started_at=cleanup_started_at,
                cleanup_finished_at=cleanup_finished_at,
                receipt_sha256=canonical_sha256(receipt_payload),
            )
            if not outcome.ownership_verified:
                raise PostgresOwnershipLost("cleanup ownership verification failed")
            if not outcome.target_verified:
                raise PostgresTargetMismatch("cleanup target verification failed")
            if outcome.residue_count != 0:
                raise PostgresCleanupResidue("cleanup left PostgreSQL scope residue")

    def _marker_for_lease(self, lease: OwnedPostgresLease) -> PostgresOwnershipMarker:
        return PostgresOwnershipMarker(
            approval_receipt_sha256=lease.approval_receipt_sha256,
            target_fingerprint=lease.target_identity.fingerprint,
            ownership_token=lease.ownership_token,
            fencing_version=lease.fencing_version,
            lease_expires_at=lease.lease_expires_at,
        )

    def assert_owned(self, lease: OwnedPostgresLease) -> None:
        self._backend.assert_owned(
            lease.scope_prefix,
            self._marker_for_lease(lease),
            now=self._clock(),
        )

    def heartbeat(self, lease: OwnedPostgresLease, *, lease_seconds: int) -> None:
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease seconds must be between 1 and 3600")
        now = self._clock()
        expires_at = now + timedelta(seconds=lease_seconds)
        updated = self._backend.heartbeat(
            lease.scope_prefix,
            self._marker_for_lease(lease),
            lease_expires_at=expires_at,
            now=now,
        )
        if not updated:
            raise PostgresOwnershipLost("PostgreSQL scope lease heartbeat was rejected")
        lease.lease_expires_at = expires_at


class Psycopg2OwnedScopeBackend:
    def __init__(self, connection_provider: ConnectionProvider) -> None:
        self._connections = connection_provider

    @contextmanager
    def _connection(self):
        try:
            with self._connections.connection() as connection:
                yield connection
        except Exception as exc:
            if getattr(exc, "pgcode", None) == "42501":
                raise PostgresPermissionDenied(
                    "PostgreSQL permission denied for approved scope operation"
                ) from None
            raise

    @staticmethod
    def _identity(cursor) -> PostgresTargetIdentity:
        cursor.execute(
            "SELECT system_identifier::text FROM pg_control_system()"
        )
        system_row = cursor.fetchone()
        if system_row is None or not str(system_row[0]).strip():
            raise PostgresTargetMismatch("PostgreSQL system identifier is unavailable")
        cursor.execute(
            "SELECT current_database(), "
            "(SELECT oid::bigint FROM pg_database "
            "WHERE datname = current_database()), "
            "current_setting('server_version_num')::int, "
            "COALESCE(inet_server_addr()::text, 'local-socket'), "
            "COALESCE(inet_server_port(), 0), current_user, current_schema()"
        )
        row = cursor.fetchone()
        if row is None:
            raise PostgresTargetMismatch("PostgreSQL target identity query returned no row")
        return PostgresTargetIdentity(
            system_identifier=str(system_row[0]),
            database_name=str(row[0]),
            database_oid=int(row[1]),
            server_version_num=int(row[2]),
            server_address=str(row[3]),
            server_port=int(row[4]),
            current_user=str(row[5]),
            current_schema=str(row[6]),
        )

    @staticmethod
    def _relations(cursor, prefix: str) -> list[tuple[str, str]]:
        escaped_prefix = prefix.replace("_", r"\_") + r"\_%"
        cursor.execute(
            "SELECT c.relname, c.relkind FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() "
            "AND c.relname LIKE %s ESCAPE '\\' "
            "AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') "
            "ORDER BY c.relname",
            (escaped_prefix,),
        )
        return [(str(row[0]), str(row[1])) for row in cursor.fetchall()]

    @staticmethod
    def _marker_table(prefix: str) -> str:
        return f"{prefix}_ownership"

    def inspect_identity(self) -> PostgresTargetIdentity:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                return self._identity(cursor)

    def create_scope(
        self,
        prefix: str,
        marker: PostgresOwnershipMarker,
    ) -> None:
        from psycopg2 import sql

        marker_table = self._marker_table(prefix)
        with self._connection() as connection:
            with connection.cursor() as cursor:
                if self._relations(cursor, prefix):
                    raise PostgresScopeNotEmpty("approved PostgreSQL scope is not empty")
                cursor.execute(
                    sql.SQL(
                        "CREATE TABLE {table} ("
                        "approval_receipt_sha256 TEXT NOT NULL, "
                        "target_fingerprint TEXT NOT NULL, "
                        "ownership_token UUID PRIMARY KEY, "
                        "fencing_version BIGINT NOT NULL, "
                        "lease_expires_at TIMESTAMPTZ NOT NULL)"
                    ).format(table=sql.Identifier(marker_table))
                )
                cursor.execute(
                    sql.SQL(
                        "INSERT INTO {table} (approval_receipt_sha256, "
                        "target_fingerprint, ownership_token, fencing_version, "
                        "lease_expires_at) VALUES (%s, %s, %s::uuid, %s, %s)"
                    ).format(table=sql.Identifier(marker_table)),
                    (
                        marker.approval_receipt_sha256,
                        marker.target_fingerprint,
                        marker.ownership_token,
                        marker.fencing_version,
                        marker.lease_expires_at,
                    ),
                )

    def _select_marker(self, cursor, prefix: str, *, for_update: bool):
        from psycopg2 import sql

        suffix = sql.SQL(" FOR UPDATE") if for_update else sql.SQL("")
        cursor.execute(
            sql.SQL(
                "SELECT approval_receipt_sha256, target_fingerprint, "
                "ownership_token::text, fencing_version, lease_expires_at "
                "FROM {table}"
            ).format(table=sql.Identifier(self._marker_table(prefix)))
            + suffix
        )
        return cursor.fetchone()

    @staticmethod
    def _marker_matches(
        row,
        marker: PostgresOwnershipMarker,
        *,
        now: datetime | None = None,
    ) -> bool:
        if row is None:
            return False
        try:
            stored_token = UUID(str(row[2])).hex
            expected_token = UUID(marker.ownership_token).hex
        except (TypeError, ValueError):
            return False
        matches = (
            hmac.compare_digest(str(row[0]), marker.approval_receipt_sha256)
            and hmac.compare_digest(str(row[1]), marker.target_fingerprint)
            and hmac.compare_digest(stored_token, expected_token)
            and int(row[3]) == marker.fencing_version
        )
        if now is not None:
            matches = matches and row[4] > now
        return matches

    def assert_owned(
        self,
        prefix: str,
        marker: PostgresOwnershipMarker,
        *,
        now: datetime,
    ) -> None:
        with self._connection() as connection:
            with connection.cursor() as cursor:
                row = self._select_marker(cursor, prefix, for_update=False)
                if not self._marker_matches(row, marker, now=now):
                    raise PostgresOwnershipLost("PostgreSQL scope ownership is not current")

    def heartbeat(
        self,
        prefix: str,
        marker: PostgresOwnershipMarker,
        *,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool:
        from psycopg2 import sql

        with self._connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET lease_expires_at = %s "
                        "WHERE ownership_token = %s::uuid "
                        "AND target_fingerprint = %s "
                        "AND fencing_version = %s AND lease_expires_at > %s"
                    ).format(table=sql.Identifier(self._marker_table(prefix))),
                    (
                        lease_expires_at,
                        marker.ownership_token,
                        marker.target_fingerprint,
                        marker.fencing_version,
                        now,
                    ),
                )
                return cursor.rowcount == 1

    def cleanup_owned(
        self,
        prefix: str,
        marker: PostgresOwnershipMarker,
    ) -> CleanupOutcome:
        from psycopg2 import sql

        drop_order = {"v": 0, "m": 1, "f": 2, "r": 3, "p": 3, "S": 4}
        drop_keyword = {
            "v": "VIEW",
            "m": "MATERIALIZED VIEW",
            "f": "FOREIGN TABLE",
            "r": "TABLE",
            "p": "TABLE",
            "S": "SEQUENCE",
        }
        with self._connection() as connection:
            with connection.cursor() as cursor:
                identity = self._identity(cursor)
                target_verified = hmac.compare_digest(
                    identity.fingerprint,
                    marker.target_fingerprint,
                )
                if not target_verified:
                    return CleanupOutcome(False, False, 0, 0, 0)
                row = self._select_marker(cursor, prefix, for_update=True)
                ownership_verified = self._marker_matches(row, marker)
                if not ownership_verified:
                    return CleanupOutcome(False, True, 0, 0, len(self._relations(cursor, prefix)))
                relations = self._relations(cursor, prefix)
                for name, kind in sorted(
                    relations,
                    key=lambda item: (drop_order[item[1]], item[0]),
                ):
                    cursor.execute(
                        sql.SQL(
                            "DROP "
                            + drop_keyword[kind]
                            + " IF EXISTS {relation} CASCADE"
                        ).format(
                            relation=sql.Identifier(name)
                        )
                    )
                residue = len(self._relations(cursor, prefix))
                return CleanupOutcome(
                    ownership_verified=True,
                    target_verified=True,
                    resources_examined=len(relations),
                    resources_removed=len(relations) - residue,
                    residue_count=residue,
                )
