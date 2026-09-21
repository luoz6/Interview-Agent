"""PostgreSQL adapter for the neutral Agent invocation ledger.

The adapter lives beside the existing runtime persistence stores and uses the
same connection provider/transaction boundary.  It is not a second database
subsystem; the schema adapter owns the ``*_agent_invocations`` relation.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.adapters.postgres.connections import ConnectionProvider
from app.adapters.postgres.runtime_repository_support import postgres_sql
from app.domain.execution_lease import LeaseBusy, LeaseLost, LeaseToken
from app.domain.interview.scheduling.ledger import (
    InvocationCommitReceipt,
    InvocationIdentity,
    InvocationLedgerEntry,
    accept_commit,
)


class PostgresAgentInvocationLedgerAdapter:
    """Durable implementation of :class:`AgentInvocationLedgerPort`."""

    _COLUMNS = (
        "execution_id, task_id, logical_attempt, status, agent_id, skill, "
        "request_digest, artifact_ref, error_result, lease_owner, lease_token, "
        "lease_expires_at, fencing_version, created_at, updated_at, started_at, finished_at"
    )

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        *,
        table_name: str = "interview_agent_invocations",
    ) -> None:
        self._provider = connection_provider
        self.table_name = table_name

    def get(self, identity: InvocationIdentity) -> InvocationLedgerEntry | None:
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        f"SELECT {self._COLUMNS} FROM {{table}} "
                        "WHERE execution_id=%s AND task_id=%s AND logical_attempt=%s"
                    ).format(table=sql.Identifier(self.table_name)),
                    identity.key,
                )
                row = cursor.fetchone()
        return self._row_to_entry(row) if row else None

    def prepare(self, entry: InvocationLedgerEntry) -> InvocationLedgerEntry:
        sql = postgres_sql()
        identity = entry.identity
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        INSERT INTO {table} (
                            execution_id, task_id, logical_attempt, status,
                            agent_id, skill, request_digest, artifact_ref,
                            error_result, lease_owner, lease_token,
                            lease_expires_at, fencing_version, created_at,
                            updated_at, started_at, finished_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (execution_id, task_id, logical_attempt) DO NOTHING
                        """
                    ).format(table=sql.Identifier(self.table_name)),
                    self._entry_params(entry),
                )
        return self.get(identity) or entry

    def mark_running(
        self,
        identity: InvocationIdentity,
        *,
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationLedgerEntry:
        updated = self._update_status(
            identity,
            status="RUNNING",
            values=(lease_owner, fencing_version),
            predicate=(
                "status IN ('PREPARED', 'RUNNING') "
                "AND lease_owner=%s AND fencing_version=%s "
                "AND lease_expires_at>NOW()"
            ),
            assignments="lease_owner=%s, fencing_version=%s, started_at=COALESCE(started_at,NOW()), updated_at=NOW()",
            predicate_values=(lease_owner, fencing_version),
        )
        if not updated:
            raise LeaseLost("stale worker cannot mark invocation running")
        return self._require(identity)

    def mark_completed(
        self,
        identity: InvocationIdentity,
        *,
        artifact_ref: str,
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationLedgerEntry:
        self.commit(
            identity,
            artifact_ref=artifact_ref,
            lease_owner=lease_owner,
            fencing_version=fencing_version,
        )
        return self._require(identity)

    def commit(
        self,
        identity: InvocationIdentity,
        *,
        artifact_ref: str,
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationCommitReceipt:
        current = self._require(identity)
        if current.status == "COMPLETED":
            candidate = InvocationCommitReceipt(
                identity=identity,
                artifact_ref=artifact_ref,
                fencing_version=fencing_version,
            )
            return accept_commit(
                InvocationCommitReceipt(
                    identity=identity,
                    artifact_ref=current.artifact_ref or "",
                    fencing_version=max(current.fencing_version, 1),
                    committed_at=current.finished_at or current.updated_at,
                ),
                candidate,
            )
        if current.status != "RUNNING":
            raise LeaseLost("invocation is not running under a lease")
        if current.lease_owner != lease_owner or current.fencing_version != fencing_version:
            raise LeaseLost("stale worker cannot commit invocation")
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET status='COMPLETED', artifact_ref=%s, "
                        "updated_at=NOW(), finished_at=NOW() WHERE execution_id=%s "
                        "AND task_id=%s AND logical_attempt=%s AND status='RUNNING' "
                        "AND lease_owner=%s AND fencing_version=%s "
                        "AND lease_expires_at>NOW() RETURNING finished_at"
                    ).format(table=sql.Identifier(self.table_name)),
                    (artifact_ref, *identity.key, lease_owner, fencing_version),
                )
                row = cursor.fetchone()
        if not row:
            raise LeaseLost("invocation commit lost its fencing race")
        return InvocationCommitReceipt(
            identity=identity,
            artifact_ref=artifact_ref,
            fencing_version=fencing_version,
            committed_at=row[0] or datetime.now(timezone.utc),
        )

    def mark_failed(
        self,
        identity: InvocationIdentity,
        *,
        error_result: dict[str, object],
        lease_owner: str,
        fencing_version: int,
    ) -> InvocationLedgerEntry:
        updated = self._update_status(
            identity,
            status="FAILED",
            values=(json.dumps(error_result),),
            predicate=(
                "status='RUNNING' AND lease_owner=%s AND fencing_version=%s "
                "AND lease_expires_at>NOW()"
            ),
            assignments="error_result=%s::jsonb, lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL, updated_at=NOW(), finished_at=NOW()",
            predicate_values=(lease_owner, fencing_version),
        )
        if not updated:
            raise LeaseLost("stale worker cannot fail invocation")
        return self._require(identity)

    def acquire(
        self,
        identity: InvocationIdentity,
        *,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> LeaseToken:
        if isinstance(lease_seconds, bool) or lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        current = self._require(identity)
        resolved_now = now or datetime.now(timezone.utc)
        if current.status == "COMPLETED":
            raise LeaseLost("completed invocation cannot be acquired")
        if current.lease_expires_at and current.lease_expires_at > resolved_now:
            if current.lease_owner != owner_id:
                raise LeaseBusy("invocation lease is owned by another worker")
            if current.lease_token:
                return LeaseToken(
                    resource_id=self._resource_id(identity),
                    owner_id=owner_id,
                    token=current.lease_token,
                    fencing_version=max(current.fencing_version, 1),
                    expires_at=current.lease_expires_at,
                )
        token = uuid.uuid4().hex
        fence = current.fencing_version + 1
        expires = resolved_now + timedelta(seconds=lease_seconds)
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET status='RUNNING', lease_owner=%s, lease_token=%s, "
                        "lease_expires_at=%s, fencing_version=%s, started_at=COALESCE(started_at,NOW()), "
                        "updated_at=NOW(), error_result=NULL, finished_at=NULL "
                        "WHERE execution_id=%s AND task_id=%s AND logical_attempt=%s "
                        "AND status IN ('PREPARED','RUNNING','FAILED') "
                        "AND fencing_version=%s "
                        "AND (lease_expires_at IS NULL OR lease_expires_at<=%s) "
                        "RETURNING fencing_version"
                    ).format(table=sql.Identifier(self.table_name)),
                    (
                        owner_id,
                        token,
                        expires,
                        fence,
                        *identity.key,
                        current.fencing_version,
                        resolved_now,
                    ),
                )
                acquired = cursor.fetchone()
        if not acquired:
            winner = self._require(identity)
            if winner.status == "COMPLETED":
                raise LeaseLost("completed invocation cannot be acquired")
            if (
                winner.lease_expires_at is not None
                and winner.lease_expires_at > resolved_now
                and winner.lease_owner != owner_id
            ):
                raise LeaseBusy("invocation lease is owned by another worker")
            raise LeaseLost("invocation lease acquisition lost its fencing race")
        return LeaseToken(
            resource_id=self._resource_id(identity),
            owner_id=owner_id,
            token=token,
            fencing_version=fence,
            expires_at=expires,
        )

    def renew(
        self,
        identity: InvocationIdentity,
        lease: LeaseToken,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> LeaseToken:
        current = self._require(identity)
        resolved_now = now or datetime.now(timezone.utc)
        self._assert_token(identity, current, lease, resolved_now)
        expires = resolved_now + timedelta(seconds=lease_seconds)
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET lease_expires_at=%s, updated_at=NOW() "
                        "WHERE execution_id=%s AND task_id=%s AND logical_attempt=%s "
                        "AND lease_owner=%s AND lease_token=%s AND fencing_version=%s "
                        "AND lease_expires_at>%s"
                    ).format(table=sql.Identifier(self.table_name)),
                    (expires, *identity.key, lease.owner_id, lease.token, lease.fencing_version, resolved_now),
                )
                renewed = cursor.rowcount == 1
        if not renewed:
            raise LeaseLost("invocation lease renewal lost its fencing race")
        return LeaseToken(
            resource_id=lease.resource_id,
            owner_id=lease.owner_id,
            token=lease.token,
            fencing_version=lease.fencing_version,
            expires_at=expires,
        )

    def expire(self, identity: InvocationIdentity, *, now: datetime | None = None) -> bool:
        resolved_now = now or datetime.now(timezone.utc)
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL, "
                        "updated_at=NOW() WHERE execution_id=%s AND task_id=%s AND logical_attempt=%s "
                        "AND lease_expires_at<=%s"
                    ).format(table=sql.Identifier(self.table_name)),
                    (*identity.key, resolved_now),
                )
                return cursor.rowcount == 1

    def reclaim(
        self,
        identity: InvocationIdentity,
        *,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> LeaseToken:
        resolved_now = now or datetime.now(timezone.utc)
        current = self._require(identity)
        if current.lease_expires_at and current.lease_expires_at > resolved_now:
            raise LeaseBusy("cannot reclaim a live invocation lease")
        return self.acquire(
            identity,
            owner_id=owner_id,
            lease_seconds=lease_seconds,
            now=resolved_now,
        )

    def assert_lease(
        self,
        identity: InvocationIdentity,
        lease: LeaseToken,
        *,
        now: datetime | None = None,
    ) -> None:
        current = self._require(identity)
        self._assert_token(identity, current, lease, now or datetime.now(timezone.utc))

    def delete_execution(self, execution_id: str) -> int:
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "DELETE FROM {table} WHERE execution_id=%s"
                    ).format(table=sql.Identifier(self.table_name)),
                    (execution_id,),
                )
                return cursor.rowcount

    def _require(self, identity: InvocationIdentity) -> InvocationLedgerEntry:
        entry = self.get(identity)
        if entry is None:
            raise KeyError(f"unknown invocation identity: {identity.key}")
        return entry

    @staticmethod
    def _resource_id(identity: InvocationIdentity) -> str:
        return ":".join((identity.execution_id, identity.task_id, str(identity.logical_attempt)))

    @staticmethod
    def _assert_token(
        identity: InvocationIdentity,
        current: InvocationLedgerEntry,
        lease: LeaseToken,
        now: datetime,
    ) -> None:
        current_token = LeaseToken(
            resource_id=PostgresAgentInvocationLedgerAdapter._resource_id(identity),
            owner_id=current.lease_owner or "",
            token=current.lease_token or "",
            fencing_version=max(current.fencing_version, 1),
            expires_at=current.lease_expires_at or now,
        )
        lease.assert_current(current_token, now=now)

    def _update_status(
        self,
        identity: InvocationIdentity,
        *,
        status: str,
        values: tuple[Any, ...],
        predicate: str,
        assignments: str,
        predicate_values: tuple[Any, ...] = (),
    ) -> bool:
        sql = postgres_sql()
        with self._provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {table} SET status=%s, {assignments} WHERE execution_id=%s "
                        "AND task_id=%s AND logical_attempt=%s AND {predicate}"
                    ).format(
                        table=sql.Identifier(self.table_name),
                        assignments=sql.SQL(assignments),
                        predicate=sql.SQL(predicate),
                    ),
                    (status, *values, *identity.key, *predicate_values),
                )
                updated = cursor.rowcount == 1
        if self.get(identity) is None:
            raise KeyError(f"unknown invocation identity: {identity.key}")
        return updated

    @classmethod
    def _entry_params(cls, entry: InvocationLedgerEntry) -> tuple[Any, ...]:
        return (
            *entry.identity.key,
            entry.status,
            entry.agent_id,
            entry.skill,
            entry.request_digest,
            entry.artifact_ref,
            json.dumps(entry.error_result) if entry.error_result is not None else None,
            entry.lease_owner,
            entry.lease_token,
            entry.lease_expires_at,
            entry.fencing_version,
            entry.created_at,
            entry.updated_at,
            entry.started_at,
            entry.finished_at,
        )

    @staticmethod
    def _row_to_entry(row: tuple[Any, ...]) -> InvocationLedgerEntry:
        return InvocationLedgerEntry(
            identity=InvocationIdentity(
                execution_id=row[0], task_id=row[1], logical_attempt=row[2]
            ),
            status=row[3],
            agent_id=row[4],
            skill=row[5],
            request_digest=row[6],
            artifact_ref=row[7],
            error_result=row[8],
            lease_owner=row[9],
            lease_token=row[10],
            lease_expires_at=row[11],
            fencing_version=row[12],
            created_at=row[13],
            updated_at=row[14],
            started_at=row[15],
            finished_at=row[16],
        )


__all__ = ["PostgresAgentInvocationLedgerAdapter"]
