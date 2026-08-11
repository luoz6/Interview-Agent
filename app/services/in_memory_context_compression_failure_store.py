from __future__ import annotations

from datetime import datetime, timedelta, timezone
import secrets
from threading import RLock

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
    replace_authorization_decisions,
)
from app.services.context_compression_failure_transitions import (
    claim_failure_state_probe,
    failure_state_decision,
    preview_failure_state,
    release_failure_state_probe,
    replace_failure_record as replace_record,
    transition_failure_state,
    transition_success_state,
    verify_failure_state_decision,
    verify_failure_state_probe,
)


class InMemoryContextCompressionFailureStore:
    def __init__(self, *, clock=None) -> None:
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._records: dict[str, FailureStateRecord] = {}
        self._lock = RLock()

    def get(self, state_key_sha256: str) -> FailureStateRecord | None:
        with self._lock:
            return self._records.get(state_key_sha256)

    def before_attempt(
        self,
        *,
        scope: FailureScope,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateDecision:
        with self._lock:
            return self._before_attempt(
                scope,
                worker_id=worker_id,
                now=now,
                lease_seconds=lease_seconds,
            )

    def _before_attempt(
        self,
        scope: FailureScope,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateDecision:
        record = self._records.get(scope.state_key_sha256)
        allow, reason, requires_probe = preview_failure_state(
            scope,
            record,
            now=now,
        )
        if not allow:
            return self._decision(
                scope,
                record,
                allow=False,
                reason=reason,
            )
        if requires_probe:
            return self._claim_probe(
                scope,
                record,
                worker_id=worker_id,
                now=now,
                lease_seconds=lease_seconds,
            )
        return self._decision(
            scope,
            record,
            allow=True,
            reason="closed",
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
        with self._lock:
            snapshot = dict(self._records)
            provider = self._before_attempt(
                provider_scope,
                worker_id=worker_id,
                now=now,
                lease_seconds=lease_seconds,
            )
            validation = self._before_attempt(
                validation_scope,
                worker_id=worker_id,
                now=now,
                lease_seconds=lease_seconds,
            )
            locked_keys = tuple(
                sorted(
                    (
                        provider_scope.state_key_sha256,
                        validation_scope.state_key_sha256,
                    )
                )
            )
            if not provider.allow_provider_call or not validation.allow_provider_call:
                self._records = snapshot
                blocked = provider if not provider.allow_provider_call else validation
                return AttemptAuthorization(
                    allow_provider_call=False,
                    reason=blocked.reason,
                    provider_scope=provider_scope,
                    validation_scope=validation_scope,
                    provider_decision=None,
                    validation_decision=None,
                    locked_state_keys=locked_keys,
                )
            reason = (
                "half_open_probe"
                if provider.probe_token or validation.probe_token
                else "closed"
            )
            return AttemptAuthorization(
                allow_provider_call=True,
                reason=reason,
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
        with self._lock:
            return self._record_failure(
                scope,
                failure_code=failure_code,
                decision=decision,
                threshold=threshold,
                cooldown_seconds=cooldown_seconds,
                now=now,
            )

    def _record_failure(
        self,
        scope: FailureScope,
        *,
        failure_code: str,
        decision: FailureStateDecision,
        threshold: int,
        cooldown_seconds: int,
        now: datetime,
    ) -> FailureStateRecord:
        current = self._records.get(scope.state_key_sha256)
        self._verify_decision(scope, current, decision, now=now)
        record = transition_failure_state(
            scope,
            current,
            decision_reason=decision.reason,
            failure_code=failure_code,
            threshold=threshold,
            cooldown_seconds=cooldown_seconds,
            now=now,
        )
        self._records[scope.state_key_sha256] = record
        return record

    def record_success(
        self,
        *,
        scope: FailureScope,
        decision: FailureStateDecision,
        now: datetime,
    ) -> FailureStateRecord:
        with self._lock:
            return self._record_success(scope, decision=decision, now=now)

    def _record_success(
        self,
        scope: FailureScope,
        *,
        decision: FailureStateDecision,
        now: datetime,
    ) -> FailureStateRecord:
        current = self._records.get(scope.state_key_sha256)
        self._verify_decision(scope, current, decision, now=now)
        record = transition_success_state(
            scope,
            current,
            now=now,
        )
        self._records[scope.state_key_sha256] = record
        return record

    def heartbeat_probe(
        self,
        *,
        scope: FailureScope,
        decision: FailureStateDecision,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateDecision:
        with self._lock:
            current = self._records.get(scope.state_key_sha256)
            self._verify_probe(current, decision, now=now)
            renewed = replace_record(
                current,
                probe_lease_until=now + timedelta(seconds=lease_seconds),
                state_version=current.state_version + 1,
                updated_at=now,
            )
            self._records[scope.state_key_sha256] = renewed
            return self._decision(
                scope,
                renewed,
                allow=True,
                reason="half_open_probe",
            )

    def heartbeat_attempt(
        self,
        *,
        authorization: AttemptAuthorization,
        now: datetime,
        lease_seconds: int,
    ) -> AttemptAuthorization | bool:
        with self._lock:
            snapshot = dict(self._records)
            try:
                provider = self._heartbeat_if_probe(
                    authorization.provider_scope,
                    authorization.provider_decision,
                    now=now,
                    lease_seconds=lease_seconds,
                )
                validation = self._heartbeat_if_probe(
                    authorization.validation_scope,
                    authorization.validation_decision,
                    now=now,
                    lease_seconds=lease_seconds,
                )
            except FailureStateLeaseLost:
                self._records = snapshot
                return False
            return replace_authorization_decisions(
                authorization,
                provider_decision=provider,
                validation_decision=validation,
            )

    def _heartbeat_if_probe(
        self,
        scope: FailureScope,
        decision: FailureStateDecision | None,
        *,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateDecision | None:
        if decision is None or decision.probe_token is None:
            return decision
        return self.heartbeat_probe(
            scope=scope,
            decision=decision,
            now=now,
            lease_seconds=lease_seconds,
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
        with self._lock:
            snapshot = dict(self._records)
            try:
                if authorization.provider_decision is None or authorization.validation_decision is None:
                    raise FailureStateLeaseLost("attempt authorization is not active")
                if outcome == "provider_failed":
                    provider = self._record_failure(
                        authorization.provider_scope,
                        failure_code=failure_code or "provider_unavailable",
                        decision=authorization.provider_decision,
                        threshold=provider_threshold,
                        cooldown_seconds=provider_cooldown_seconds,
                        now=now,
                    )
                    validation = self._release_or_close(
                        authorization.validation_scope,
                        authorization.validation_decision,
                        now=now,
                    )
                elif outcome == "validation_failed":
                    provider = self._record_success(
                        authorization.provider_scope,
                        decision=authorization.provider_decision,
                        now=now,
                    )
                    validation = self._record_failure(
                        authorization.validation_scope,
                        failure_code=failure_code or "invalid_schema",
                        decision=authorization.validation_decision,
                        threshold=validation_threshold,
                        cooldown_seconds=validation_cooldown_seconds,
                        now=now,
                    )
                elif outcome == "success":
                    provider = self._record_success(
                        authorization.provider_scope,
                        decision=authorization.provider_decision,
                        now=now,
                    )
                    validation = self._record_success(
                        authorization.validation_scope,
                        decision=authorization.validation_decision,
                        now=now,
                    )
                else:
                    raise ValueError("invalid attempt outcome")
            except BaseException:
                self._records = snapshot
                raise
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
        with self._lock:
            snapshot = dict(self._records)
            released = 0
            try:
                for scope, decision in (
                    (authorization.provider_scope, authorization.provider_decision),
                    (authorization.validation_scope, authorization.validation_decision),
                ):
                    if decision is None or decision.probe_token is None:
                        continue
                    current = self._records.get(scope.state_key_sha256)
                    self._verify_probe(current, decision, now=now, require_live=False)
                    self._records[scope.state_key_sha256] = replace_record(
                        current,
                        state="open",
                        probe_owner_sha256=None,
                        probe_token=None,
                        probe_lease_until=None,
                        state_version=current.state_version + 1,
                        updated_at=now,
                    )
                    released += 1
            except BaseException:
                self._records = snapshot
                raise
            return AttemptAbortResult(released_probe_count=released)

    def delete_owner(
        self,
        *,
        privacy_scope_sha256: str,
        owner_type: str,
        owner_key_sha256: str,
    ) -> int:
        with self._lock:
            keys = [
                key
                for key, record in self._records.items()
                if record.privacy_scope_sha256 == privacy_scope_sha256
                and record.owner_type == owner_type
                and record.owner_key_sha256 == owner_key_sha256
            ]
            for key in keys:
                del self._records[key]
            return len(keys)

    def cleanup_expired(
        self,
        *,
        before: datetime,
        now: datetime,
        batch_size: int,
    ) -> int:
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")
        with self._lock:
            keys = []
            for key, record in sorted(
                self._records.items(), key=lambda item: item[1].updated_at
            ):
                if record.updated_at >= before:
                    continue
                if (
                    record.state == "half_open"
                    and record.probe_lease_until is not None
                    and record.probe_lease_until > now
                ):
                    continue
                keys.append(key)
                if len(keys) >= batch_size:
                    break
            for key in keys:
                del self._records[key]
            return len(keys)

    def _claim_probe(
        self,
        scope: FailureScope,
        record: FailureStateRecord,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> FailureStateDecision:
        claimed = claim_failure_state_probe(
            record,
            probe_token=secrets.token_urlsafe(32),
            worker_id=worker_id,
            now=now,
            lease_seconds=lease_seconds,
        )
        self._records[scope.state_key_sha256] = claimed
        return self._decision(
            scope,
            claimed,
            allow=True,
            reason="half_open_probe",
        )

    def _verify_decision(
        self,
        scope: FailureScope,
        current: FailureStateRecord | None,
        decision: FailureStateDecision,
        *,
        now: datetime,
    ) -> None:
        verify_failure_state_decision(scope, current, decision, now=now)

    @staticmethod
    def _verify_probe(
        current: FailureStateRecord | None,
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

    def _release_or_close(
        self,
        scope: FailureScope,
        decision: FailureStateDecision,
        *,
        now: datetime,
    ) -> FailureStateRecord:
        current = self._records.get(scope.state_key_sha256)
        self._verify_decision(scope, current, decision, now=now)
        if current is None:
            return self._record_success(scope, decision=decision, now=now)
        released = release_failure_state_probe(current, now=now)
        if released is not current:
            self._records[scope.state_key_sha256] = released
            return released
        return current

    @staticmethod
    def _decision(
        scope: FailureScope,
        record: FailureStateRecord | None,
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
