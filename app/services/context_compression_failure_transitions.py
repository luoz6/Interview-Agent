from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256

from app.services.context_compression_failure_containment import (
    FailureScope,
    FailureStateDecision,
    FailureStateLeaseLost,
    FailureStateRecord,
)


def replace_failure_record(
    record: FailureStateRecord,
    **changes,
) -> FailureStateRecord:
    values = {
        name: getattr(record, name)
        for name in FailureStateRecord.__dataclass_fields__
    }
    values.update(changes)
    return FailureStateRecord(**values)


def failure_state_decision(
    scope: FailureScope,
    record: FailureStateRecord | None,
    *,
    allow: bool,
    reason: str,
) -> FailureStateDecision:
    return FailureStateDecision(
        allow_provider_call=allow,
        reason=reason,
        state_key_sha256=scope.state_key_sha256,
        state_version=record.state_version if record else 0,
        fencing_version=record.fencing_version if record else 0,
        probe_owner_sha256=record.probe_owner_sha256 if record else None,
        probe_token=record.probe_token if record else None,
        probe_lease_until=record.probe_lease_until if record else None,
    )


def preview_failure_state(
    scope: FailureScope,
    record: FailureStateRecord | None,
    *,
    now: datetime,
) -> tuple[bool, str, bool]:
    if record is None or record.state == "closed":
        return True, "closed", False
    if record.state == "open":
        if record.open_until is not None and now < record.open_until:
            reason = (
                "provider_circuit_open"
                if scope.kind == "provider_circuit"
                else "validation_quarantine_open"
            )
            return False, reason, False
        return True, "half_open_probe", True
    if record.probe_lease_until is not None and now < record.probe_lease_until:
        return False, "half_open_probe_owned", False
    return True, "half_open_probe", True


def verify_failure_state_probe(
    current: FailureStateRecord | None,
    decision: FailureStateDecision,
    *,
    now: datetime,
    require_live: bool = True,
) -> None:
    if (
        current is None
        or current.state != "half_open"
        or current.state_version != decision.state_version
        or current.fencing_version != decision.fencing_version
        or current.probe_token != decision.probe_token
    ):
        raise FailureStateLeaseLost("failure state probe was fenced")
    if require_live and (
        current.probe_lease_until is None
        or current.probe_lease_until <= now
    ):
        raise FailureStateLeaseLost("failure state probe lease expired")


def verify_failure_state_decision(
    scope: FailureScope,
    current: FailureStateRecord | None,
    decision: FailureStateDecision,
    *,
    now: datetime,
) -> None:
    if decision.state_key_sha256 != scope.state_key_sha256:
        raise FailureStateLeaseLost("failure state decision has the wrong key")
    if decision.probe_token is not None:
        verify_failure_state_probe(current, decision, now=now)
        return
    current_version = current.state_version if current else 0
    current_fence = current.fencing_version if current else 0
    if (
        decision.state_version != current_version
        or decision.fencing_version != current_fence
    ):
        raise FailureStateLeaseLost("failure state decision is stale")


def claim_failure_state_probe(
    record: FailureStateRecord,
    *,
    worker_id: str,
    probe_token: str,
    now: datetime,
    lease_seconds: int,
) -> FailureStateRecord:
    return replace_failure_record(
        record,
        state="half_open",
        probe_owner_sha256=sha256(worker_id.encode("utf-8")).hexdigest(),
        probe_token=probe_token,
        probe_lease_until=now + timedelta(seconds=lease_seconds),
        fencing_version=record.fencing_version + 1,
        state_version=record.state_version + 1,
        updated_at=now,
    )


def transition_failure_state(
    scope: FailureScope,
    current: FailureStateRecord | None,
    *,
    decision_reason: str,
    failure_code: str,
    threshold: int,
    cooldown_seconds: int,
    now: datetime,
) -> FailureStateRecord:
    count = (current.consecutive_failures if current else 0) + 1
    should_open = decision_reason == "half_open_probe" or count >= threshold
    return _build_terminal_record(
        scope,
        current,
        consecutive_failure_count=count,
        state="open" if should_open else "closed",
        open_until=(
            now + timedelta(seconds=cooldown_seconds)
            if should_open
            else None
        ),
        last_failure_code=failure_code,
        now=now,
    )


def transition_success_state(
    scope: FailureScope,
    current: FailureStateRecord | None,
    *,
    now: datetime,
) -> FailureStateRecord:
    return _build_terminal_record(
        scope,
        current,
        consecutive_failure_count=0,
        state="closed",
        open_until=None,
        last_failure_code=None,
        now=now,
    )


def release_failure_state_probe(
    current: FailureStateRecord,
    *,
    now: datetime,
) -> FailureStateRecord:
    if current.state != "half_open":
        return current
    return replace_failure_record(
        current,
        state="open",
        probe_owner_sha256=None,
        probe_token=None,
        probe_lease_until=None,
        state_version=current.state_version + 1,
        updated_at=now,
    )


def _build_terminal_record(
    scope: FailureScope,
    previous: FailureStateRecord | None,
    *,
    consecutive_failure_count: int,
    state: str,
    open_until: datetime | None,
    last_failure_code: str | None,
    now: datetime,
) -> FailureStateRecord:
    return FailureStateRecord(
        state_key_sha256=scope.state_key_sha256,
        kind=scope.kind,
        privacy_scope_sha256=scope.privacy_scope_sha256,
        owner_type=scope.owner_type,
        owner_key_sha256=scope.owner_key_sha256,
        provider=scope.provider,
        model=scope.model,
        artifact_type=scope.artifact_type,
        policy_version=scope.policy_version,
        source_manifest_sha256=getattr(scope, "source_manifest_sha256", None),
        compression_intent_sha256=getattr(
            scope,
            "compression_intent_sha256",
            None,
        ),
        prompt_contract_version=getattr(scope, "prompt_contract_version", None),
        output_schema_version=getattr(scope, "output_schema_version", None),
        consecutive_failure_count=consecutive_failure_count,
        state=state,
        open_until=open_until,
        probe_owner_sha256=None,
        probe_token=None,
        probe_lease_until=None,
        fencing_version=previous.fencing_version if previous else 0,
        state_version=(previous.state_version if previous else 0) + 1,
        last_failure_code=last_failure_code,
        created_at=previous.created_at if previous else now,
        updated_at=now,
    )


__all__ = [
    "claim_failure_state_probe",
    "failure_state_decision",
    "preview_failure_state",
    "release_failure_state_probe",
    "replace_failure_record",
    "transition_failure_state",
    "transition_success_state",
    "verify_failure_state_decision",
    "verify_failure_state_probe",
]
