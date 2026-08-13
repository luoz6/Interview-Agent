from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, Literal, Mapping

from app.ports.context_compression_failure_state import (
    ContextCompressionFailureStateStore,
)
from app.services.workflow_thread_lock import FencedWriteRejected


FailureStateKind = Literal["provider_circuit", "validation_quarantine"]
FailureStateStatus = Literal["closed", "open", "half_open"]
FailureOutcome = Literal["success", "provider_failed", "validation_failed"]

PROVIDER_FAILURE_CODES = frozenset(
    {"provider_timeout", "provider_connection", "provider_unavailable"}
)
VALIDATION_FAILURE_CODES = frozenset({"invalid_schema", "grounding_failed"})
NON_COUNTED_FAILURE_CODES = frozenset(
    {
        "artifact_busy",
        "artifact_reused",
        "parent_lease_lost",
        "parent_or_runtime_failed",
        "identity_conflict",
        "privacy_scope_invalid",
        "cancelled",
        "provider_unclassified",
        "stale_ownership",
        "artifact_lease_lost",
    }
)
_OWNER_TYPES = frozenset({"prep_run", "interview_session", "review_job"})
_HEX = frozenset("0123456789abcdef")


class FailureStateLeaseLost(FencedWriteRejected):
    """A fenced half-open probe no longer owns its authoritative lease."""


def _require_digest(value: object, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(character not in _HEX for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase sha256 digest")
    return value


def _require_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be canonical non-empty text")
    return value


def _require_owner(owner_type: object, owner_key: object) -> tuple[str, str]:
    owner_type = _require_text(owner_type, field_name="owner_type")
    if owner_type not in _OWNER_TYPES:
        raise ValueError("owner_type is not a supported canonical owner")
    return owner_type, _require_text(owner_key, field_name="owner_key")


def _canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ProviderCircuitScope:
    state_key_sha256: str
    kind: Literal["provider_circuit"]
    privacy_scope_sha256: str
    owner_type: str
    owner_key_sha256: str
    provider: str
    model: str
    artifact_type: str
    policy_version: str


@dataclass(frozen=True)
class ValidationQuarantineScope:
    state_key_sha256: str
    kind: Literal["validation_quarantine"]
    privacy_scope_sha256: str
    owner_type: str
    owner_key_sha256: str
    artifact_type: str
    source_manifest_sha256: str
    compression_intent_sha256: str
    prompt_contract_version: str
    output_schema_version: str
    policy_version: str
    provider: str
    model: str


FailureScope = ProviderCircuitScope | ValidationQuarantineScope


def build_provider_circuit_scope(
    *,
    privacy_scope_sha256: str,
    owner_type: str,
    owner_key: str,
    provider: str,
    model: str,
    artifact_type: str,
    policy_version: str,
) -> ProviderCircuitScope:
    privacy_scope_sha256 = _require_digest(
        privacy_scope_sha256, field_name="privacy_scope_sha256"
    )
    owner_type, owner_key = _require_owner(owner_type, owner_key)
    owner_key_sha256 = sha256(owner_key.encode("utf-8")).hexdigest()
    values = {
        "kind": "provider_circuit",
        "privacy_scope_sha256": privacy_scope_sha256,
        "owner_type": owner_type,
        "owner_key_sha256": owner_key_sha256,
        "provider": _require_text(provider, field_name="provider"),
        "model": _require_text(model, field_name="model"),
        "artifact_type": _require_text(
            artifact_type, field_name="artifact_type"
        ),
        "policy_version": _require_text(
            policy_version, field_name="policy_version"
        ),
    }
    return ProviderCircuitScope(
        state_key_sha256=_canonical_digest(values),
        **values,
    )


def build_validation_quarantine_scope(
    *,
    privacy_scope_sha256: str,
    owner_type: str,
    owner_key: str,
    artifact_type: str,
    source_manifest_sha256: str,
    compression_intent_sha256: str,
    prompt_contract_version: str,
    output_schema_version: str,
    policy_version: str,
    provider: str,
    model: str,
) -> ValidationQuarantineScope:
    privacy_scope_sha256 = _require_digest(
        privacy_scope_sha256, field_name="privacy_scope_sha256"
    )
    owner_type, owner_key = _require_owner(owner_type, owner_key)
    owner_key_sha256 = sha256(owner_key.encode("utf-8")).hexdigest()
    values = {
        "kind": "validation_quarantine",
        "privacy_scope_sha256": privacy_scope_sha256,
        "owner_type": owner_type,
        "owner_key_sha256": owner_key_sha256,
        "artifact_type": _require_text(
            artifact_type, field_name="artifact_type"
        ),
        "source_manifest_sha256": _require_digest(
            source_manifest_sha256,
            field_name="source_manifest_sha256",
        ),
        "compression_intent_sha256": _require_digest(
            compression_intent_sha256,
            field_name="compression_intent_sha256",
        ),
        "prompt_contract_version": _require_text(
            prompt_contract_version,
            field_name="prompt_contract_version",
        ),
        "output_schema_version": _require_text(
            output_schema_version,
            field_name="output_schema_version",
        ),
        "policy_version": _require_text(
            policy_version, field_name="policy_version"
        ),
        "provider": _require_text(provider, field_name="provider"),
        "model": _require_text(model, field_name="model"),
    }
    return ValidationQuarantineScope(
        state_key_sha256=_canonical_digest(values),
        **values,
    )


def _scope_from_record_values(values: Mapping[str, object]) -> FailureScope:
    common = {
        "privacy_scope_sha256": values["privacy_scope_sha256"],
        "owner_type": values["owner_type"],
        "owner_key_sha256": values["owner_key_sha256"],
        "provider": values["provider"],
        "model": values["model"],
        "artifact_type": values["artifact_type"],
        "policy_version": values["policy_version"],
    }
    kind = values["kind"]
    if kind == "provider_circuit":
        key_values = {"kind": kind, **common}
        return ProviderCircuitScope(
            state_key_sha256=_canonical_digest(key_values),
            **key_values,
        )
    if kind != "validation_quarantine":
        raise ValueError("kind must be provider_circuit or validation_quarantine")
    key_values = {
        "kind": kind,
        "privacy_scope_sha256": common["privacy_scope_sha256"],
        "owner_type": common["owner_type"],
        "owner_key_sha256": common["owner_key_sha256"],
        "artifact_type": common["artifact_type"],
        "source_manifest_sha256": values["source_manifest_sha256"],
        "compression_intent_sha256": values["compression_intent_sha256"],
        "prompt_contract_version": values["prompt_contract_version"],
        "output_schema_version": values["output_schema_version"],
        "policy_version": common["policy_version"],
        "provider": common["provider"],
        "model": common["model"],
    }
    return ValidationQuarantineScope(
        state_key_sha256=_canonical_digest(key_values),
        **key_values,
    )


@dataclass(frozen=True)
class FailureStateRecord:
    state_key_sha256: str
    kind: FailureStateKind
    privacy_scope_sha256: str
    owner_type: str
    owner_key_sha256: str
    provider: str
    model: str
    artifact_type: str
    policy_version: str
    source_manifest_sha256: str | None
    compression_intent_sha256: str | None
    prompt_contract_version: str | None
    output_schema_version: str | None
    consecutive_failure_count: int
    state: FailureStateStatus
    open_until: datetime | None
    probe_owner_sha256: str | None
    probe_token: str | None
    probe_lease_until: datetime | None
    fencing_version: int
    state_version: int
    last_failure_code: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def consecutive_failures(self) -> int:
        return self.consecutive_failure_count

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        values = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }
        if mode == "json":
            for name in (
                "open_until",
                "probe_lease_until",
                "created_at",
                "updated_at",
            ):
                value = values[name]
                if isinstance(value, datetime):
                    values[name] = value.isoformat()
        elif mode != "python":
            raise ValueError("mode must be python or json")
        return values

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "FailureStateRecord":
        values = dict(mapping)
        if "consecutive_failure_count" not in values:
            values["consecutive_failure_count"] = values.pop(
                "consecutive_failures"
            )
        for name in (
            "state_key_sha256",
            "privacy_scope_sha256",
            "owner_key_sha256",
        ):
            _require_digest(values.get(name), field_name=name)
        owner_type = _require_text(values.get("owner_type"), field_name="owner_type")
        if owner_type not in _OWNER_TYPES:
            raise ValueError("owner_type is not supported")
        for name in ("provider", "model", "artifact_type", "policy_version"):
            _require_text(values.get(name), field_name=name)
        kind = values.get("kind")
        quarantine_names = (
            "source_manifest_sha256",
            "compression_intent_sha256",
            "prompt_contract_version",
            "output_schema_version",
        )
        if kind == "provider_circuit":
            if any(values.get(name) is not None for name in quarantine_names):
                raise ValueError("provider circuit cannot own quarantine identity")
        elif kind == "validation_quarantine":
            _require_digest(
                values.get("source_manifest_sha256"),
                field_name="source_manifest_sha256",
            )
            _require_digest(
                values.get("compression_intent_sha256"),
                field_name="compression_intent_sha256",
            )
            _require_text(
                values.get("prompt_contract_version"),
                field_name="prompt_contract_version",
            )
            _require_text(
                values.get("output_schema_version"),
                field_name="output_schema_version",
            )
        else:
            raise ValueError("invalid failure state kind")
        canonical_scope = _scope_from_record_values(values)
        if values["state_key_sha256"] != canonical_scope.state_key_sha256:
            raise ValueError("state_key_sha256 is not canonical")
        count = values.get("consecutive_failure_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("consecutive_failure_count must be non-negative")
        state = values.get("state")
        if state not in {"closed", "open", "half_open"}:
            raise ValueError("invalid failure state")
        for name in ("state_version", "fencing_version"):
            item = values.get(name)
            minimum = 1 if name == "state_version" else 0
            if isinstance(item, bool) or not isinstance(item, int) or item < minimum:
                raise ValueError(f"{name} is invalid")
        probe_values = (
            values.get("probe_owner_sha256"),
            values.get("probe_token"),
            values.get("probe_lease_until"),
        )
        if state == "half_open":
            if any(item is None for item in probe_values):
                raise ValueError("half_open state requires a complete probe")
            _require_digest(
                values["probe_owner_sha256"], field_name="probe_owner_sha256"
            )
            _require_text(values["probe_token"], field_name="probe_token")
        elif any(item is not None for item in probe_values):
            raise ValueError("non-half-open state cannot own a probe")
        for name in ("created_at", "updated_at"):
            item = values.get(name)
            if not isinstance(item, datetime) or item.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        return cls(**{name: values.get(name) for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class FailureStateDecision:
    allow_provider_call: bool
    reason: str
    state_key_sha256: str
    state_version: int
    fencing_version: int
    probe_owner_sha256: str | None = None
    probe_token: str | None = None
    probe_lease_until: datetime | None = None


@dataclass(frozen=True)
class AttemptAuthorization:
    allow_provider_call: bool
    reason: str
    provider_scope: ProviderCircuitScope
    validation_scope: ValidationQuarantineScope
    provider_decision: FailureStateDecision | None
    validation_decision: FailureStateDecision | None
    locked_state_keys: tuple[str, str]

    @property
    def provider_probe(self) -> FailureStateDecision | None:
        decision = self.provider_decision
        return decision if decision and decision.probe_token else None

    @property
    def validation_probe(self) -> FailureStateDecision | None:
        decision = self.validation_decision
        return decision if decision and decision.probe_token else None

    @property
    def probe_count(self) -> int:
        return int(self.provider_probe is not None) + int(
            self.validation_probe is not None
        )


@dataclass(frozen=True)
class AttemptFinishResult:
    provider_state: FailureStateRecord
    validation_state: FailureStateRecord


@dataclass(frozen=True)
class AttemptAbortResult:
    released_probe_count: int


@dataclass(frozen=True)
class FailureContainmentConfig:
    provider_circuit_threshold: int
    provider_circuit_cooldown_seconds: int
    validation_quarantine_threshold: int
    validation_quarantine_cooldown_seconds: int
    failure_state_lease_seconds: int

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.failure_state_lease_seconds >= min(
            self.provider_circuit_cooldown_seconds,
            self.validation_quarantine_cooldown_seconds,
        ):
            raise ValueError("failure state lease must be shorter than both cooldowns")


def failure_state_metric_dimensions(record: FailureStateRecord) -> dict[str, str]:
    dimensions = {
        "kind": record.kind,
        "state": record.state,
        "store_outcome": {
            "open": "opened",
            "closed": "closed",
            "half_open": "half_open",
        }[record.state],
    }
    if record.last_failure_code is not None:
        dimensions["failure_code"] = record.last_failure_code
    return dimensions


class ContextCompressionFailureContainment:
    def __init__(
        self,
        *,
        store: ContextCompressionFailureStateStore,
        config: FailureContainmentConfig,
        clock=None,
    ):
        self.store = store
        self.config = config
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def before_attempt(
        self,
        scope: FailureScope,
        *,
        worker_id: str,
    ) -> FailureStateDecision:
        _require_text(worker_id, field_name="worker_id")
        return self.store.before_attempt(
            scope=scope,
            worker_id=worker_id,
            now=self.clock(),
            lease_seconds=self.config.failure_state_lease_seconds,
        )

    def authorize_attempt(
        self,
        *,
        provider_scope: ProviderCircuitScope,
        validation_scope: ValidationQuarantineScope,
        worker_id: str,
    ) -> AttemptAuthorization:
        _require_text(worker_id, field_name="worker_id")
        return self.store.authorize_attempt(
            provider_scope=provider_scope,
            validation_scope=validation_scope,
            worker_id=worker_id,
            now=self.clock(),
            lease_seconds=self.config.failure_state_lease_seconds,
        )

    def record_failure(
        self,
        scope: FailureScope,
        *,
        failure_code: str,
        decision: FailureStateDecision,
    ) -> FailureStateRecord | None:
        if scope.kind == "provider_circuit":
            if failure_code not in PROVIDER_FAILURE_CODES:
                return None
            threshold = self.config.provider_circuit_threshold
            cooldown = self.config.provider_circuit_cooldown_seconds
        else:
            if failure_code not in VALIDATION_FAILURE_CODES:
                return None
            threshold = self.config.validation_quarantine_threshold
            cooldown = self.config.validation_quarantine_cooldown_seconds
        return self.store.record_failure(
            scope=scope,
            failure_code=failure_code,
            decision=decision,
            threshold=threshold,
            cooldown_seconds=cooldown,
            now=self.clock(),
        )

    def record_success(
        self,
        scope: FailureScope,
        *,
        decision: FailureStateDecision,
    ) -> FailureStateRecord:
        return self.store.record_success(
            scope=scope,
            decision=decision,
            now=self.clock(),
        )

    def heartbeat_probe(
        self,
        scope: FailureScope,
        *,
        decision: FailureStateDecision,
    ) -> FailureStateDecision:
        return self.store.heartbeat_probe(
            scope=scope,
            decision=decision,
            now=self.clock(),
            lease_seconds=self.config.failure_state_lease_seconds,
        )

    def heartbeat_attempt(
        self,
        authorization: AttemptAuthorization,
    ) -> AttemptAuthorization | bool:
        return self.store.heartbeat_attempt(
            authorization=authorization,
            now=self.clock(),
            lease_seconds=self.config.failure_state_lease_seconds,
        )

    def finish_attempt(
        self,
        authorization: AttemptAuthorization,
        *,
        outcome: FailureOutcome,
        failure_code: str | None = None,
    ) -> AttemptFinishResult:
        if outcome == "provider_failed" and failure_code not in PROVIDER_FAILURE_CODES:
            raise ValueError("provider_failed requires a provider failure code")
        if outcome == "validation_failed" and failure_code not in VALIDATION_FAILURE_CODES:
            raise ValueError("validation_failed requires a validation failure code")
        if outcome == "success" and failure_code is not None:
            raise ValueError("success cannot include a failure code")
        return self.store.finish_attempt(
            authorization=authorization,
            outcome=outcome,
            failure_code=failure_code,
            provider_threshold=self.config.provider_circuit_threshold,
            provider_cooldown_seconds=(
                self.config.provider_circuit_cooldown_seconds
            ),
            validation_threshold=self.config.validation_quarantine_threshold,
            validation_cooldown_seconds=(
                self.config.validation_quarantine_cooldown_seconds
            ),
            now=self.clock(),
        )

    def abort_attempt(
        self,
        authorization: AttemptAuthorization,
        *,
        reason: str,
    ) -> AttemptAbortResult:
        if reason not in NON_COUNTED_FAILURE_CODES:
            raise ValueError("abort reason must be a stable non-counted code")
        return self.store.abort_attempt(
            authorization=authorization,
            reason=reason,
            now=self.clock(),
        )


def replace_authorization_decisions(
    authorization: AttemptAuthorization,
    *,
    provider_decision: FailureStateDecision | None,
    validation_decision: FailureStateDecision | None,
) -> AttemptAuthorization:
    return replace(
        authorization,
        provider_decision=provider_decision,
        validation_decision=validation_decision,
    )
