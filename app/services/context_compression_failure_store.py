from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import secrets
from typing import Any

from app.services.context_compression_failure_containment import (
    AttemptAbortResult,
    AttemptAuthorization,
    AttemptFinishResult,
    FailureScope,
    FailureStateDecision,
    FailureStateLeaseLost,
    FailureStateRecord,
    ProviderCircuitScope,
    ValidationQuarantineScope,
)
from app.services.context_compression_failure_transitions import (
    claim_failure_state_probe,
    failure_state_decision,
    preview_failure_state,
    release_failure_state_probe,
    transition_failure_state,
    transition_success_state,
    verify_failure_state_decision,
    verify_failure_state_probe,
)
from app.services.postgres_connections import (
    ConnectionProvider,
    DirectPsycopg2ConnectionProvider,
)
from app.services.postgres_identifiers import validate_runtime_table_prefix
from app.services.postgres_schema import resolve_schema_mode, validate_relations


class FailureStateConflict(RuntimeError):
    """A direct PostgreSQL failure-state CAS predicate did not match."""


_COLUMN_NAMES = tuple(FailureStateRecord.__dataclass_fields__)
_SELECT_COLUMNS = ", ".join(_COLUMN_NAMES)


class PostgresContextCompressionFailureStore:
    """Durable, fenced provider-circuit and validation-quarantine store."""

    def __init__(
        self,
        *,
        dsn: str | None = None,
        connection_provider: ConnectionProvider | None = None,
        table_prefix: str = "interview",
        schema_mode: str | None = None,
    ) -> None:
        validate_runtime_table_prefix(table_prefix)
        if connection_provider is None:
            if not dsn:
                raise ValueError("dsn or connection_provider is required")
            connection_provider = DirectPsycopg2ConnectionProvider(dsn)
            self._provider_is_owned = True
        else:
            self._provider_is_owned = False
        self.dsn = dsn or ""
        self._connection_provider = connection_provider
        self.table_prefix = table_prefix
        self.table = f"{table_prefix}_context_compression_failure_states"
        self.schema_mode = resolve_schema_mode(
            schema_mode,
            provider_is_owned=self._provider_is_owned,
        )
        if self.schema_mode == "migrate":
            self._ensure_schema()
        else:
            validate_relations(self._connection_provider, (self.table,))

    def get(self, state_key_sha256: str) -> FailureStateRecord | None:
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        "SELECT {columns} FROM {table} "
                        "WHERE state_key_sha256 = %s"
                    ),
                    (state_key_sha256,),
                )
                row = cursor.fetchone()
        return self._record_from_row(row)

    def before_attempt(
        self,
        *,
        scope: FailureScope,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateDecision:
        self._require_positive(lease_seconds, "lease_seconds")
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                current = self._ensure_and_lock(cursor, scope, now=now)
                return self._before_locked(
                    cursor,
                    scope,
                    current,
                    worker_id=worker_id,
                    now=now,
                    lease_seconds=lease_seconds,
                )

    def authorize_attempt(
        self,
        *,
        provider_scope: ProviderCircuitScope,
        validation_scope: ValidationQuarantineScope,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> AttemptAuthorization:
        self._require_positive(lease_seconds, "lease_seconds")
        scopes = tuple(
            sorted(
                (provider_scope, validation_scope),
                key=lambda item: item.state_key_sha256,
            )
        )
        locked_keys = tuple(item.state_key_sha256 for item in scopes)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                records = {
                    scope.state_key_sha256: self._ensure_and_lock(
                        cursor,
                        scope,
                        now=now,
                    )
                    for scope in scopes
                }
                previews = {
                    scope.state_key_sha256: self._preview(
                        scope,
                        records[scope.state_key_sha256],
                        now=now,
                    )
                    for scope in scopes
                }
                blocked = next(
                    (
                        previews[scope.state_key_sha256]
                        for scope in (provider_scope, validation_scope)
                        if not previews[scope.state_key_sha256][0]
                    ),
                    None,
                )
                if blocked is not None:
                    return AttemptAuthorization(
                        allow_provider_call=False,
                        reason=blocked[1],
                        provider_scope=provider_scope,
                        validation_scope=validation_scope,
                        provider_decision=None,
                        validation_decision=None,
                        locked_state_keys=locked_keys,
                    )
                decisions: dict[str, FailureStateDecision] = {}
                for scope in scopes:
                    decisions[scope.state_key_sha256] = self._before_locked(
                        cursor,
                        scope,
                        records[scope.state_key_sha256],
                        worker_id=worker_id,
                        now=now,
                        lease_seconds=lease_seconds,
                    )
                provider = decisions[provider_scope.state_key_sha256]
                validation = decisions[validation_scope.state_key_sha256]
                return AttemptAuthorization(
                    allow_provider_call=True,
                    reason=(
                        "half_open_probe"
                        if provider.probe_token or validation.probe_token
                        else "closed"
                    ),
                    provider_scope=provider_scope,
                    validation_scope=validation_scope,
                    provider_decision=provider,
                    validation_decision=validation,
                    locked_state_keys=locked_keys,
                )

    def record_failure(
        self,
        *,
        scope: FailureScope,
        failure_code: str,
        decision: FailureStateDecision,
        threshold: int,
        cooldown_seconds: int,
        now: datetime,
    ) -> FailureStateRecord:
        self._require_positive(threshold, "threshold")
        self._require_positive(cooldown_seconds, "cooldown_seconds")
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                current = self._lock_existing(cursor, scope.state_key_sha256)
                return self._record_failure_locked(
                    cursor,
                    scope,
                    current,
                    failure_code=failure_code,
                    decision=decision,
                    threshold=threshold,
                    cooldown_seconds=cooldown_seconds,
                    now=now,
                )

    def record_success(
        self,
        *,
        scope: FailureScope,
        decision: FailureStateDecision,
        now: datetime,
    ) -> FailureStateRecord:
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                current = self._lock_existing(cursor, scope.state_key_sha256)
                return self._record_success_locked(
                    cursor,
                    scope,
                    current,
                    decision=decision,
                    now=now,
                )

    def heartbeat_probe(
        self,
        *,
        scope: FailureScope,
        decision: FailureStateDecision,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateDecision:
        self._require_positive(lease_seconds, "lease_seconds")
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                current = self._lock_existing(cursor, scope.state_key_sha256)
                renewed = self._heartbeat_probe_locked(
                    cursor,
                    scope,
                    current,
                    decision=decision,
                    now=now,
                    lease_seconds=lease_seconds,
                )
        return self._decision(scope, renewed, allow=True, reason="half_open_probe")

    def heartbeat_attempt(
        self,
        *,
        authorization: AttemptAuthorization,
        now: datetime,
        lease_seconds: int,
    ) -> AttemptAuthorization | bool:
        self._require_positive(lease_seconds, "lease_seconds")
        if (
            authorization.provider_decision is None
            or authorization.validation_decision is None
        ):
            return False
        pairs = self._sorted_authorization_pairs(authorization)
        try:
            with self._connection_provider.connection() as connection:
                with connection.cursor() as cursor:
                    locked = {
                        scope.state_key_sha256: self._lock_existing(
                            cursor,
                            scope.state_key_sha256,
                        )
                        for scope, _ in pairs
                    }
                    decisions: dict[str, FailureStateDecision] = {}
                    for scope, decision in pairs:
                        if decision.probe_token is None:
                            self._verify_decision(
                                scope,
                                locked[scope.state_key_sha256],
                                decision,
                                now=now,
                            )
                            decisions[scope.state_key_sha256] = decision
                            continue
                        renewed = self._heartbeat_probe_locked(
                            cursor,
                            scope,
                            locked[scope.state_key_sha256],
                            decision=decision,
                            now=now,
                            lease_seconds=lease_seconds,
                        )
                        decisions[scope.state_key_sha256] = self._decision(
                            scope,
                            renewed,
                            allow=True,
                            reason="half_open_probe",
                        )
        except FailureStateLeaseLost:
            return False
        return replace(
            authorization,
            provider_decision=decisions[
                authorization.provider_scope.state_key_sha256
            ],
            validation_decision=decisions[
                authorization.validation_scope.state_key_sha256
            ],
        )

    def finish_attempt(
        self,
        *,
        authorization: AttemptAuthorization,
        outcome: str,
        failure_code: str | None,
        provider_threshold: int,
        provider_cooldown_seconds: int,
        validation_threshold: int,
        validation_cooldown_seconds: int,
        now: datetime,
    ) -> AttemptFinishResult:
        if (
            authorization.provider_decision is None
            or authorization.validation_decision is None
        ):
            raise FailureStateLeaseLost("attempt authorization is not active")
        pairs = self._sorted_authorization_pairs(authorization)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                locked = {
                    scope.state_key_sha256: self._lock_existing(
                        cursor,
                        scope.state_key_sha256,
                    )
                    for scope, _ in pairs
                }
                provider_scope = authorization.provider_scope
                validation_scope = authorization.validation_scope
                provider_decision = authorization.provider_decision
                validation_decision = authorization.validation_decision
                if outcome == "provider_failed":
                    provider = self._record_failure_locked(
                        cursor,
                        provider_scope,
                        locked[provider_scope.state_key_sha256],
                        failure_code=failure_code or "provider_unavailable",
                        decision=provider_decision,
                        threshold=provider_threshold,
                        cooldown_seconds=provider_cooldown_seconds,
                        now=now,
                    )
                    validation = self._release_or_close_locked(
                        cursor,
                        validation_scope,
                        locked[validation_scope.state_key_sha256],
                        decision=validation_decision,
                        now=now,
                    )
                elif outcome == "validation_failed":
                    provider = self._record_success_locked(
                        cursor,
                        provider_scope,
                        locked[provider_scope.state_key_sha256],
                        decision=provider_decision,
                        now=now,
                    )
                    validation = self._record_failure_locked(
                        cursor,
                        validation_scope,
                        locked[validation_scope.state_key_sha256],
                        failure_code=failure_code or "invalid_schema",
                        decision=validation_decision,
                        threshold=validation_threshold,
                        cooldown_seconds=validation_cooldown_seconds,
                        now=now,
                    )
                elif outcome == "success":
                    provider = self._record_success_locked(
                        cursor,
                        provider_scope,
                        locked[provider_scope.state_key_sha256],
                        decision=provider_decision,
                        now=now,
                    )
                    validation = self._record_success_locked(
                        cursor,
                        validation_scope,
                        locked[validation_scope.state_key_sha256],
                        decision=validation_decision,
                        now=now,
                    )
                else:
                    raise ValueError("invalid attempt outcome")
        return AttemptFinishResult(
            provider_state=provider,
            validation_state=validation,
        )

    def abort_attempt(
        self,
        *,
        authorization: AttemptAuthorization,
        reason: str,
        now: datetime,
    ) -> AttemptAbortResult:
        del reason
        released = 0
        pairs = self._sorted_authorization_pairs(authorization)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                locked = {
                    scope.state_key_sha256: self._lock_existing(
                        cursor,
                        scope.state_key_sha256,
                    )
                    for scope, _ in pairs
                }
                for scope, decision in pairs:
                    if decision.probe_token is None:
                        continue
                    current = locked[scope.state_key_sha256]
                    self._verify_probe(current, decision, now=now, require_live=False)
                    updated = replace(
                        current,
                        state="open",
                        probe_owner_sha256=None,
                        probe_token=None,
                        probe_lease_until=None,
                        state_version=current.state_version + 1,
                        updated_at=now,
                    )
                    self._write_record(
                        cursor,
                        current,
                        updated,
                        decision=decision,
                        require_live=False,
                    )
                    released += 1
        return AttemptAbortResult(released_probe_count=released)

    def reset(
        self,
        *,
        state_key_sha256: str,
        expected_state_version: int,
        expected_fencing_version: int,
        probe_token: str,
        now: datetime,
    ) -> FailureStateRecord:
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        "UPDATE {table} SET consecutive_failure_count=0, "
                        "state='closed', open_until=NULL, probe_owner_sha256=NULL, "
                        "probe_token=NULL, probe_lease_until=NULL, "
                        "last_failure_code=NULL, state_version=state_version+1, "
                        "updated_at=%s WHERE state_key_sha256=%s "
                        "AND state='half_open' AND state_version=%s "
                        "AND fencing_version=%s AND probe_token=%s "
                        "AND probe_lease_until>%s "
                        "RETURNING {columns}"
                    ),
                    (
                        now,
                        state_key_sha256,
                        expected_state_version,
                        expected_fencing_version,
                        probe_token,
                        now,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise FailureStateConflict("failure state reset conflicted")
        record = self._record_from_row(row)
        if record is None:
            raise FailureStateConflict("failure state reset conflicted")
        return record

    def reclaim_expired_probe(
        self,
        *,
        state_key_sha256: str,
        expected_state_version: int,
        expected_fencing_version: int,
        probe_owner_sha256: str,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateRecord:
        self._require_positive(lease_seconds, "lease_seconds")
        token = secrets.token_urlsafe(32)
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        "UPDATE {table} SET probe_owner_sha256=%s, probe_token=%s, "
                        "probe_lease_until=%s, fencing_version=fencing_version+1, "
                        "state_version=state_version+1, updated_at=%s "
                        "WHERE state_key_sha256=%s AND state='half_open' "
                        "AND state_version=%s AND fencing_version=%s "
                        "AND probe_lease_until<=%s RETURNING {columns}"
                    ),
                    (
                        probe_owner_sha256,
                        token,
                        now + timedelta(seconds=lease_seconds),
                        now,
                        state_key_sha256,
                        expected_state_version,
                        expected_fencing_version,
                        now,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise FailureStateConflict("failure state reclaim conflicted")
        record = self._record_from_row(row)
        if record is None:
            raise FailureStateConflict("failure state reclaim conflicted")
        return record

    def delete_owner(
        self,
        *,
        privacy_scope_sha256: str,
        owner_type: str,
        owner_key_sha256: str,
    ) -> int:
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        "DELETE FROM {table} WHERE privacy_scope_sha256=%s "
                        "AND owner_type=%s AND owner_key_sha256=%s"
                    ),
                    (privacy_scope_sha256, owner_type, owner_key_sha256),
                )
                return int(cursor.rowcount)

    def cleanup_expired(
        self,
        *,
        before: datetime,
        now: datetime,
        batch_size: int,
    ) -> int:
        self._require_positive(batch_size, "batch_size")
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        "DELETE FROM {table} WHERE state_key_sha256 IN ("
                        "SELECT state_key_sha256 FROM {table} "
                        "WHERE updated_at<%s AND NOT (state='half_open' "
                        "AND probe_lease_until>%s) "
                        "ORDER BY updated_at,state_key_sha256 LIMIT %s "
                        "FOR UPDATE SKIP LOCKED)"
                    ),
                    (before, now, batch_size),
                )
                return int(cursor.rowcount)

    def _before_locked(
        self,
        cursor: Any,
        scope: FailureScope,
        record: FailureStateRecord,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateDecision:
        allow, reason, requires_probe = self._preview(scope, record, now=now)
        if not allow:
            return self._decision(scope, record, allow=False, reason=reason)
        if not requires_probe:
            return self._decision(scope, record, allow=True, reason="closed")
        claimed = claim_failure_state_probe(
            record,
            worker_id=worker_id,
            probe_token=secrets.token_urlsafe(32),
            now=now,
            lease_seconds=lease_seconds,
        )
        self._write_record(cursor, record, claimed)
        return self._decision(
            scope,
            claimed,
            allow=True,
            reason="half_open_probe",
        )

    @staticmethod
    def _preview(
        scope: FailureScope,
        record: FailureStateRecord,
        *,
        now: datetime,
    ) -> tuple[bool, str, bool]:
        return preview_failure_state(scope, record, now=now)

    def _record_failure_locked(
        self,
        cursor: Any,
        scope: FailureScope,
        current: FailureStateRecord,
        *,
        failure_code: str,
        decision: FailureStateDecision,
        threshold: int,
        cooldown_seconds: int,
        now: datetime,
    ) -> FailureStateRecord:
        self._verify_decision(scope, current, decision, now=now)
        updated = transition_failure_state(
            scope,
            current,
            decision_reason=decision.reason,
            failure_code=failure_code,
            threshold=threshold,
            cooldown_seconds=cooldown_seconds,
            now=now,
        )
        return self._write_record(cursor, current, updated, decision=decision)

    def _record_success_locked(
        self,
        cursor: Any,
        scope: FailureScope,
        current: FailureStateRecord,
        *,
        decision: FailureStateDecision,
        now: datetime,
    ) -> FailureStateRecord:
        self._verify_decision(scope, current, decision, now=now)
        updated = transition_success_state(
            scope,
            current,
            now=now,
        )
        return self._write_record(cursor, current, updated, decision=decision)

    def _heartbeat_probe_locked(
        self,
        cursor: Any,
        scope: FailureScope,
        current: FailureStateRecord,
        *,
        decision: FailureStateDecision,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateRecord:
        self._verify_probe(current, decision, now=now)
        renewed = replace(
            current,
            probe_lease_until=now + timedelta(seconds=lease_seconds),
            state_version=current.state_version + 1,
            updated_at=now,
        )
        return self._write_record(cursor, current, renewed, decision=decision)

    def _release_or_close_locked(
        self,
        cursor: Any,
        scope: FailureScope,
        current: FailureStateRecord,
        *,
        decision: FailureStateDecision,
        now: datetime,
    ) -> FailureStateRecord:
        self._verify_decision(scope, current, decision, now=now)
        released = release_failure_state_probe(current, now=now)
        if released is current:
            return current
        return self._write_record(
            cursor,
            current,
            released,
            decision=decision,
            require_live=False,
        )

    def _ensure_and_lock(
        self,
        cursor: Any,
        scope: FailureScope,
        *,
        now: datetime,
    ) -> FailureStateRecord:
        cursor.execute(
            self._sql(
                "INSERT INTO {table} ({columns}) VALUES ("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "0,'closed',NULL,NULL,NULL,NULL,0,1,NULL,%s,%s) "
                "ON CONFLICT (state_key_sha256) DO NOTHING"
            ),
            self._scope_values(scope) + (now, now),
        )
        record = self._lock_existing(cursor, scope.state_key_sha256)
        if record is None:
            raise FailureStateConflict("failure state row could not be locked")
        if not self._record_matches_scope(record, scope):
            raise FailureStateConflict("failure state identity conflicted")
        return record

    def _lock_existing(
        self,
        cursor: Any,
        state_key_sha256: str,
    ) -> FailureStateRecord:
        cursor.execute(
            self._sql(
                "SELECT {columns} FROM {table} WHERE state_key_sha256=%s FOR UPDATE"
            ),
            (state_key_sha256,),
        )
        row = cursor.fetchone()
        record = self._record_from_row(row)
        if record is None:
            raise FailureStateLeaseLost("failure state decision is stale")
        return record

    def _write_record(
        self,
        cursor: Any,
        previous: FailureStateRecord,
        updated: FailureStateRecord,
        *,
        decision: FailureStateDecision | None = None,
        require_live: bool = True,
    ) -> FailureStateRecord:
        where = (
            "state_key_sha256=%s AND state_version=%s AND fencing_version=%s"
        )
        where_values: tuple[Any, ...] = (
            previous.state_key_sha256,
            previous.state_version,
            previous.fencing_version,
        )
        if decision is not None and decision.probe_token is not None:
            where += " AND state='half_open' AND probe_token=%s"
            where_values += (decision.probe_token,)
            if require_live:
                where += " AND probe_lease_until>%s"
                where_values += (updated.updated_at,)
        cursor.execute(
            self._sql(
                "UPDATE {table} SET consecutive_failure_count=%s,state=%s,"
                "open_until=%s,probe_owner_sha256=%s,probe_token=%s,"
                "probe_lease_until=%s,fencing_version=%s,state_version=%s,"
                "last_failure_code=%s,updated_at=%s WHERE "
                + where
                + " RETURNING {columns}"
            ),
            (
                updated.consecutive_failure_count,
                updated.state,
                updated.open_until,
                updated.probe_owner_sha256,
                updated.probe_token,
                updated.probe_lease_until,
                updated.fencing_version,
                updated.state_version,
                updated.last_failure_code,
                updated.updated_at,
            )
            + where_values,
        )
        row = cursor.fetchone()
        record = self._record_from_row(row)
        if record is None:
            raise FailureStateLeaseLost("failure state mutation was fenced")
        return record

    @staticmethod
    def _verify_decision(
        scope: FailureScope,
        current: FailureStateRecord,
        decision: FailureStateDecision,
        *,
        now: datetime,
    ) -> None:
        verify_failure_state_decision(scope, current, decision, now=now)

    @staticmethod
    def _verify_probe(
        current: FailureStateRecord,
        decision: FailureStateDecision,
        *,
        now: datetime,
        require_live: bool = True,
    ) -> None:
        verify_failure_state_probe(
            current,
            decision,
            now=now,
            require_live=require_live,
        )

    @staticmethod
    def _decision(
        scope: FailureScope,
        record: FailureStateRecord,
        *,
        allow: bool,
        reason: str,
    ) -> FailureStateDecision:
        return failure_state_decision(
            scope,
            record,
            allow=allow,
            reason=reason,
        )

    @staticmethod
    def _scope_values(scope: FailureScope) -> tuple[Any, ...]:
        return (
            scope.state_key_sha256,
            scope.kind,
            scope.privacy_scope_sha256,
            scope.owner_type,
            scope.owner_key_sha256,
            scope.provider,
            scope.model,
            scope.artifact_type,
            scope.policy_version,
            getattr(scope, "source_manifest_sha256", None),
            getattr(scope, "compression_intent_sha256", None),
            getattr(scope, "prompt_contract_version", None),
            getattr(scope, "output_schema_version", None),
        )

    @staticmethod
    def _record_matches_scope(
        record: FailureStateRecord,
        scope: FailureScope,
    ) -> bool:
        expected = PostgresContextCompressionFailureStore._scope_values(scope)
        actual = tuple(getattr(record, name) for name in _COLUMN_NAMES[:13])
        return actual == expected

    @staticmethod
    def _sorted_authorization_pairs(
        authorization: AttemptAuthorization,
    ) -> tuple[tuple[FailureScope, FailureStateDecision], ...]:
        if (
            authorization.provider_decision is None
            or authorization.validation_decision is None
        ):
            raise FailureStateLeaseLost("attempt authorization is not active")
        return tuple(
            sorted(
                (
                    (
                        authorization.provider_scope,
                        authorization.provider_decision,
                    ),
                    (
                        authorization.validation_scope,
                        authorization.validation_decision,
                    ),
                ),
                key=lambda item: item[0].state_key_sha256,
            )
        )

    @staticmethod
    def _record_from_row(row: Any) -> FailureStateRecord | None:
        if row is None:
            return None
        if isinstance(row, dict):
            mapping = row
        else:
            mapping = dict(zip(_COLUMN_NAMES, row))
        return FailureStateRecord.from_mapping(mapping)

    def _ensure_schema(self) -> None:
        from psycopg2 import sql

        owner_index = f"{self.table_prefix}_cc_failure_states_owner_idx"
        cleanup_index = f"{self.table_prefix}_cc_failure_states_cleanup_idx"
        with self._connection_provider.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {table} (
                            state_key_sha256 CHAR(64) PRIMARY KEY,
                            kind TEXT NOT NULL CHECK (
                                kind IN ('provider_circuit','validation_quarantine')
                            ),
                            privacy_scope_sha256 CHAR(64) NOT NULL,
                            owner_type TEXT NOT NULL,
                            owner_key_sha256 CHAR(64) NOT NULL,
                            provider TEXT NOT NULL,
                            model TEXT NOT NULL,
                            artifact_type TEXT NOT NULL,
                            policy_version TEXT NOT NULL,
                            source_manifest_sha256 CHAR(64),
                            compression_intent_sha256 CHAR(64),
                            prompt_contract_version TEXT,
                            output_schema_version TEXT,
                            consecutive_failure_count BIGINT NOT NULL DEFAULT 0
                                CHECK (consecutive_failure_count >= 0),
                            state TEXT NOT NULL DEFAULT 'closed'
                                CHECK (state IN ('closed','open','half_open')),
                            open_until TIMESTAMPTZ,
                            probe_owner_sha256 CHAR(64),
                            probe_token TEXT,
                            probe_lease_until TIMESTAMPTZ,
                            fencing_version BIGINT NOT NULL DEFAULT 0,
                            state_version BIGINT NOT NULL DEFAULT 1,
                            last_failure_code TEXT,
                            created_at TIMESTAMPTZ NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL,
                            CHECK (state_version > 0 AND fencing_version >= 0),
                            CHECK (
                                (state='half_open' AND probe_owner_sha256 IS NOT NULL
                                 AND probe_token IS NOT NULL
                                 AND probe_lease_until IS NOT NULL)
                                OR
                                (state<>'half_open' AND probe_owner_sha256 IS NULL
                                 AND probe_token IS NULL
                                 AND probe_lease_until IS NULL)
                            ),
                            CHECK (
                                (kind='provider_circuit'
                                 AND source_manifest_sha256 IS NULL
                                 AND compression_intent_sha256 IS NULL
                                 AND prompt_contract_version IS NULL
                                 AND output_schema_version IS NULL)
                                OR
                                (kind='validation_quarantine'
                                 AND source_manifest_sha256 IS NOT NULL
                                 AND compression_intent_sha256 IS NOT NULL
                                 AND prompt_contract_version IS NOT NULL
                                 AND output_schema_version IS NOT NULL)
                            )
                        )
                        """
                    ).format(table=sql.Identifier(self.table))
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index} ON {table} "
                        "(privacy_scope_sha256,owner_type,owner_key_sha256)"
                    ).format(
                        index=sql.Identifier(owner_index),
                        table=sql.Identifier(self.table),
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index} ON {table} "
                        "(open_until,probe_lease_until,updated_at)"
                    ).format(
                        index=sql.Identifier(cleanup_index),
                        table=sql.Identifier(self.table),
                    )
                )

    def _sql(self, template: str):
        from psycopg2 import sql

        return sql.SQL(template).format(
            table=sql.Identifier(self.table),
            columns=sql.SQL(_SELECT_COLUMNS),
        )

    @staticmethod
    def _require_positive(value: int, field_name: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field_name} must be a positive integer")


__all__ = ["FailureStateConflict", "PostgresContextCompressionFailureStore"]
