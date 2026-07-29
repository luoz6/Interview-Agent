from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

from app.services.context_artifacts import (
    ArtifactPurpose,
    ContextArtifactBusy,
    ContextArtifactClaim,
    ContextArtifactCleanupPolicy,
    ContextArtifactCleanupResult,
    ContextArtifactConflict,
    ContextArtifactIdentity,
    ContextArtifactLeaseLost,
    ContextArtifactMissing,
    ContextArtifactRecord,
    ContextArtifactRef,
    OwnerType,
    artifact_payload_sha256,
    parse_artifact_payload,
)


_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PURPOSE_CONTRACT = {
    "prep_plan_context": ("prep_run", "prep_context"),
    "interview_conversation_context": (
        "interview_session",
        "question_conversation",
    ),
    "interview_evidence_context": (
        "interview_session",
        "evidence_compression",
    ),
    "review_context": ("review_job", "question_conversation"),
    "review_evidence_context": ("review_job", "evidence_compression"),
}


@dataclass
class _ArtifactRow:
    artifact_id: str
    identity: ContextArtifactIdentity
    status: str
    attempt_count: int
    fencing_version: int
    claim_owner: str | None
    claim_token: str | None
    claim_expires_at: datetime | None
    output_sha256: str | None
    payload: dict[str, Any] | None
    last_error_code: str | None
    created_at: datetime
    completed_at: datetime | None
    updated_at: datetime


@dataclass
class _OwnerRefRow:
    artifact_ref: str
    artifact_id: str
    owner_type: OwnerType
    owner_key: str
    purpose: ArtifactPurpose
    artifact_sha256: str
    created_at: datetime
    last_used_at: datetime
    retain_until: datetime | None


class InMemoryContextArtifactStore:
    """Thread-safe deterministic reference implementation of the Store port."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        artifact_id_factory: Callable[[], str] | None = None,
        claim_token_factory: Callable[[], str] | None = None,
        ref_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._artifact_id_factory = artifact_id_factory or (
            lambda: str(uuid4())
        )
        self._claim_token_factory = claim_token_factory or (
            lambda: str(uuid4())
        )
        self._ref_id_factory = ref_id_factory or (lambda: str(uuid4()))
        self._rows_by_key: dict[str, _ArtifactRow] = {}
        self._rows_by_id: dict[str, _ArtifactRow] = {}
        self._refs: dict[str, _OwnerRefRow] = {}
        self._ref_identity: dict[tuple[str, str, str, str], str] = {}
        self._lock = RLock()

    def claim(
        self,
        identity: ContextArtifactIdentity,
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> ContextArtifactClaim:
        self._require_nonempty(worker_id, "worker_id")
        self._require_positive(lease_seconds, "lease_seconds")
        now = self._now()
        with self._lock:
            row = self._rows_by_key.get(identity.artifact_key)
            if row is None:
                row = _ArtifactRow(
                    artifact_id=self._new_value(
                        self._artifact_id_factory, "artifact_id"
                    ),
                    identity=identity,
                    status="running",
                    attempt_count=1,
                    fencing_version=1,
                    claim_owner=worker_id,
                    claim_token=self._new_value(
                        self._claim_token_factory, "claim_token"
                    ),
                    claim_expires_at=now + timedelta(seconds=lease_seconds),
                    output_sha256=None,
                    payload=None,
                    last_error_code=None,
                    created_at=now,
                    completed_at=None,
                    updated_at=now,
                )
                self._rows_by_key[identity.artifact_key] = row
                self._rows_by_id[row.artifact_id] = row
                return self._claim_from_row(row)

            self._assert_identity(row, identity)
            if row.status == "completed":
                return self._claim_from_row(row)
            if (
                row.status == "running"
                and row.claim_expires_at is not None
                and row.claim_expires_at > now
            ):
                raise ContextArtifactBusy("context artifact has a live claim")

            row.status = "running"
            row.attempt_count += 1
            row.fencing_version += 1
            row.claim_owner = worker_id
            row.claim_token = self._new_value(
                self._claim_token_factory, "claim_token"
            )
            row.claim_expires_at = now + timedelta(seconds=lease_seconds)
            row.output_sha256 = None
            row.payload = None
            row.last_error_code = None
            row.completed_at = None
            row.updated_at = now
            return self._claim_from_row(row)

    def heartbeat(
        self,
        claim: ContextArtifactClaim,
        *,
        lease_seconds: int,
    ) -> bool:
        self._require_positive(lease_seconds, "lease_seconds")
        now = self._now()
        with self._lock:
            row = self._rows_by_id.get(claim.artifact_id)
            if row is None or not self._claim_is_owned(row, claim, now):
                return False
            row.claim_expires_at = now + timedelta(seconds=lease_seconds)
            row.updated_at = now
            return True

    def complete(
        self,
        claim: ContextArtifactClaim,
        payload: dict[str, Any],
    ) -> ContextArtifactRecord:
        now = self._now()
        with self._lock:
            row = self._owned_row(claim, now)
            try:
                validated = parse_artifact_payload(
                    row.identity.material.artifact_type,
                    payload,
                )
            except Exception as exc:
                raise ContextArtifactConflict(
                    "context artifact payload schema conflicts with identity"
                ) from exc
            if (
                validated.schema_version
                != row.identity.material.output_schema_version
            ):
                raise ContextArtifactConflict(
                    "context artifact output schema conflicts with identity"
                )
            stored_payload = validated.model_dump(mode="json")
            output_sha256 = artifact_payload_sha256(validated)
            row.status = "completed"
            row.output_sha256 = output_sha256
            row.payload = deepcopy(stored_payload)
            row.last_error_code = None
            row.completed_at = now
            row.claim_owner = None
            row.claim_token = None
            row.claim_expires_at = None
            row.updated_at = now
            return self._record_from_row(row)

    def fail(
        self,
        claim: ContextArtifactClaim,
        *,
        error_code: str,
    ) -> None:
        if _ERROR_CODE_RE.fullmatch(error_code) is None:
            raise ValueError("error_code must be a stable machine code")
        now = self._now()
        with self._lock:
            row = self._owned_row(claim, now)
            row.status = "failed"
            row.output_sha256 = None
            row.payload = None
            row.last_error_code = error_code
            row.completed_at = None
            row.claim_owner = None
            row.claim_token = None
            row.claim_expires_at = None
            row.updated_at = now

    def get_terminal_by_key(
        self,
        artifact_key: str,
    ) -> ContextArtifactRecord | None:
        with self._lock:
            row = self._rows_by_key.get(artifact_key)
            if row is None or row.status not in {"completed", "failed"}:
                return None
            return self._record_from_row(row)

    def create_owner_ref(
        self,
        record: ContextArtifactRecord,
        *,
        owner_type: OwnerType,
        owner_key: str,
        purpose: ArtifactPurpose,
        retain_until: datetime | None = None,
    ) -> ContextArtifactRef:
        self._require_nonempty(owner_key, "owner_key")
        purpose_contract = _PURPOSE_CONTRACT.get(purpose)
        if purpose_contract is None:
            raise ContextArtifactConflict("context artifact purpose is unsupported")
        expected_owner, expected_artifact_type = purpose_contract
        if (
            owner_type != expected_owner
            or record.identity.material.artifact_type != expected_artifact_type
        ):
            raise ContextArtifactConflict(
                "context artifact owner purpose conflicts with artifact type"
            )
        if retain_until is not None:
            self._require_aware(retain_until, "retain_until")
            if owner_type != "prep_run":
                raise ValueError("retain_until is only valid for prep_run references")
        now = self._now()
        with self._lock:
            row = self._rows_by_id.get(record.artifact_id)
            if row is None:
                raise ContextArtifactMissing("context artifact record is missing")
            if row.status != "completed":
                raise ContextArtifactConflict(
                    "only a completed context artifact can be referenced"
                )
            authoritative = self._record_from_row(row)
            if authoritative != record:
                raise ContextArtifactConflict(
                    "context artifact record conflicts with stored state"
                )
            key = (owner_type, owner_key, purpose, row.artifact_id)
            existing_ref = self._ref_identity.get(key)
            if existing_ref is not None:
                existing = self._refs[existing_ref]
                existing.last_used_at = now
                if retain_until is not None and (
                    existing.retain_until is None
                    or retain_until > existing.retain_until
                ):
                    existing.retain_until = retain_until
                return self._public_ref(existing, row)

            ref_id = self._new_value(self._ref_id_factory, "ref_id")
            artifact_ref = f"context-artifact-ref:{ref_id}"
            ref_row = _OwnerRefRow(
                artifact_ref=artifact_ref,
                artifact_id=row.artifact_id,
                owner_type=owner_type,
                owner_key=owner_key,
                purpose=purpose,
                artifact_sha256=row.output_sha256 or "",
                created_at=now,
                last_used_at=now,
                retain_until=retain_until,
            )
            self._refs[artifact_ref] = ref_row
            self._ref_identity[key] = artifact_ref
            return self._public_ref(ref_row, row)

    def load_ref(
        self,
        ref: ContextArtifactRef,
        *,
        owner_type: OwnerType,
        owner_key: str,
        purpose: ArtifactPurpose,
        expected_identity: ContextArtifactIdentity,
    ) -> ContextArtifactRecord:
        with self._lock:
            ref_row = self._refs.get(ref.artifact_ref)
            if ref_row is None:
                raise ContextArtifactMissing("context artifact reference is missing")
            row = self._rows_by_id.get(ref_row.artifact_id)
            if row is None:
                raise ContextArtifactMissing("context artifact record is missing")
            if (
                ref_row.owner_type != owner_type
                or ref_row.owner_key != owner_key
                or ref_row.purpose != purpose
                or _PURPOSE_CONTRACT.get(ref_row.purpose)
                != (
                    ref_row.owner_type,
                    row.identity.material.artifact_type,
                )
                or row.identity != expected_identity
                or row.identity.material != expected_identity.material
                or row.status != "completed"
                or row.output_sha256 is None
                or row.payload is None
                or ref.artifact_sha256 != ref_row.artifact_sha256
                or ref.artifact_sha256 != row.output_sha256
                or ref.artifact_type != row.identity.material.artifact_type
                or ref.compression_policy_version
                != row.identity.material.compression_policy_version
            ):
                raise ContextArtifactConflict(
                    "context artifact reference conflicts with expected identity"
                )
            try:
                validated = parse_artifact_payload(
                    row.identity.material.artifact_type,
                    row.payload,
                )
            except Exception as exc:
                raise ContextArtifactConflict(
                    "context artifact payload schema conflicts with reference"
                ) from exc
            if (
                validated.schema_version
                != row.identity.material.output_schema_version
                or artifact_payload_sha256(validated) != row.output_sha256
            ):
                raise ContextArtifactConflict(
                    "context artifact payload digest conflicts with reference"
                )
            ref_row.last_used_at = self._now()
            return self._record_from_row(row)

    def delete_owner_refs(
        self,
        *,
        owner_type: OwnerType,
        owner_key: str,
    ) -> int:
        with self._lock:
            selected = [
                artifact_ref
                for artifact_ref, ref in self._refs.items()
                if ref.owner_type == owner_type and ref.owner_key == owner_key
            ]
            for artifact_ref in selected:
                self._delete_ref(artifact_ref)
            return len(selected)

    def cleanup(
        self,
        policy: ContextArtifactCleanupPolicy,
    ) -> ContextArtifactCleanupResult:
        with self._lock:
            remaining = policy.batch_size
            deleted_refs = 0
            expired_refs = sorted(
                (
                    ref
                    for ref in self._refs.values()
                    if ref.owner_type == "prep_run"
                    and ref.retain_until is not None
                    and ref.retain_until <= policy.prep_ref_expires_before
                ),
                key=lambda ref: (ref.retain_until, ref.artifact_ref),
            )
            for ref in expired_refs[:remaining]:
                self._delete_ref(ref.artifact_ref)
                deleted_refs += 1
                remaining -= 1

            referenced_ids = {ref.artifact_id for ref in self._refs.values()}
            deleted_completed = 0
            deleted_failed = 0
            if remaining:
                completed = sorted(
                    (
                        row
                        for row in self._rows_by_key.values()
                        if row.status == "completed"
                        and row.artifact_id not in referenced_ids
                        and row.completed_at is not None
                        and row.completed_at < policy.completed_before
                    ),
                    key=lambda row: (row.completed_at, row.artifact_id),
                )
                for row in completed[:remaining]:
                    self._delete_artifact(row)
                    deleted_completed += 1
                    remaining -= 1

            if remaining:
                failed = sorted(
                    (
                        row
                        for row in self._rows_by_key.values()
                        if row.status == "failed"
                        and row.artifact_id not in referenced_ids
                        and row.updated_at < policy.failed_before
                    ),
                    key=lambda row: (row.updated_at, row.artifact_id),
                )
                for row in failed[:remaining]:
                    self._delete_artifact(row)
                    deleted_failed += 1
                    remaining -= 1

            return ContextArtifactCleanupResult(
                deleted_owner_refs=deleted_refs,
                deleted_completed_artifacts=deleted_completed,
                deleted_failed_artifacts=deleted_failed,
            )

    def _owned_row(
        self,
        claim: ContextArtifactClaim,
        now: datetime,
    ) -> _ArtifactRow:
        row = self._rows_by_id.get(claim.artifact_id)
        if row is None or not self._claim_is_owned(row, claim, now):
            raise ContextArtifactLeaseLost("context artifact claim was lost")
        return row

    @staticmethod
    def _claim_is_owned(
        row: _ArtifactRow,
        claim: ContextArtifactClaim,
        now: datetime,
    ) -> bool:
        return (
            row.status == "running"
            and claim.status == "running"
            and row.identity.artifact_key == claim.artifact_key
            and row.claim_token == claim.claim_token
            and row.claim_owner == claim.claim_owner
            and row.fencing_version == claim.fencing_version
            and row.claim_expires_at is not None
            and row.claim_expires_at > now
        )

    @staticmethod
    def _assert_identity(
        row: _ArtifactRow,
        identity: ContextArtifactIdentity,
    ) -> None:
        if row.identity != identity or row.identity.material != identity.material:
            raise ContextArtifactConflict(
                "context artifact key conflicts with immutable identity"
            )

    @staticmethod
    def _claim_from_row(row: _ArtifactRow) -> ContextArtifactClaim:
        if row.status == "completed":
            InMemoryContextArtifactStore._validate_completed_row(row)
            return ContextArtifactClaim(
                artifact_id=row.artifact_id,
                artifact_key=row.identity.artifact_key,
                status="completed",
                claim_token=None,
                fencing_version=row.fencing_version,
                claim_owner=None,
                output_sha256=row.output_sha256,
                payload=deepcopy(row.payload),
            )
        return ContextArtifactClaim(
            artifact_id=row.artifact_id,
            artifact_key=row.identity.artifact_key,
            status="running",
            claim_token=row.claim_token,
            fencing_version=row.fencing_version,
            claim_owner=row.claim_owner,
            output_sha256=None,
            payload=None,
        )

    @staticmethod
    def _record_from_row(row: _ArtifactRow) -> ContextArtifactRecord:
        try:
            return ContextArtifactRecord(
                artifact_id=row.artifact_id,
                identity=row.identity,
                status=row.status,  # type: ignore[arg-type]
                output_sha256=row.output_sha256,
                payload=deepcopy(row.payload),
                last_error_code=row.last_error_code,
                completed_at=row.completed_at,
            )
        except (TypeError, ValueError) as exc:
            raise ContextArtifactConflict(
                "context artifact terminal record is invalid"
            ) from exc

    @staticmethod
    def _validate_completed_row(row: _ArtifactRow) -> None:
        if row.output_sha256 is None or row.payload is None:
            raise ContextArtifactConflict(
                "completed context artifact output is missing"
            )
        try:
            validated = parse_artifact_payload(
                row.identity.material.artifact_type,
                row.payload,
            )
        except Exception as exc:
            raise ContextArtifactConflict(
                "completed context artifact payload schema is invalid"
            ) from exc
        if (
            validated.schema_version
            != row.identity.material.output_schema_version
            or artifact_payload_sha256(validated) != row.output_sha256
        ):
            raise ContextArtifactConflict(
                "completed context artifact output digest is invalid"
            )

    @staticmethod
    def _public_ref(
        ref: _OwnerRefRow,
        row: _ArtifactRow,
    ) -> ContextArtifactRef:
        return ContextArtifactRef(
            artifact_ref=ref.artifact_ref,
            artifact_sha256=ref.artifact_sha256,
            artifact_type=row.identity.material.artifact_type,
            compression_policy_version=(
                row.identity.material.compression_policy_version
            ),
        )

    def _delete_ref(self, artifact_ref: str) -> None:
        ref = self._refs.pop(artifact_ref)
        self._ref_identity.pop(
            (ref.owner_type, ref.owner_key, ref.purpose, ref.artifact_id),
            None,
        )

    def _delete_artifact(self, row: _ArtifactRow) -> None:
        self._rows_by_key.pop(row.identity.artifact_key, None)
        self._rows_by_id.pop(row.artifact_id, None)

    def _now(self) -> datetime:
        value = self._clock()
        self._require_aware(value, "clock")
        return value

    @staticmethod
    def _new_value(factory: Callable[[], str], field_name: str) -> str:
        value = factory()
        InMemoryContextArtifactStore._require_nonempty(value, field_name)
        return value

    @staticmethod
    def _require_nonempty(value: str, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be non-empty")

    @staticmethod
    def _require_positive(value: int, field_name: str) -> None:
        if value <= 0:
            raise ValueError(f"{field_name} must be positive")

    @staticmethod
    def _require_aware(value: datetime, field_name: str) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
