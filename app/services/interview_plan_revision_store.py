from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Protocol
from uuid import uuid4

from app.services.interview_plan_revision import (
    InterviewPlanRevision,
    InterviewPlanV2,
    PlanConfigurationSnapshot,
    PlanCreatedReason,
    PlanRevisionSourceKind,
    PlanSourcePayload,
    PlanSourceRecord,
    PlanSourceReference,
    PlanSourceReferenceType,
    plan_payload_sha256,
    source_payload_sha256,
    utc_now,
)


class PlanRevisionError(RuntimeError):
    pass


class PlanRevisionNotFound(PlanRevisionError):
    pass


class PlanRevisionConflict(PlanRevisionError):
    def __init__(self, message: str, *, current_revision: int | None = None) -> None:
        super().__init__(message)
        self.current_revision = current_revision


class PlanSourceInUse(PlanRevisionError):
    pass


class PlanSourceUnavailable(PlanRevisionError):
    pass


class InterviewPlanRevisionStore(Protocol):
    def create_initial(
        self,
        *,
        source_payload: PlanSourcePayload,
        plan: InterviewPlanV2,
        retention_policy: str,
        generator_version: str,
        plan_family_id: str | None = None,
    ) -> InterviewPlanRevision: ...

    def create_next_revision(
        self,
        *,
        plan_family_id: str,
        expected_revision: int,
        plan: InterviewPlanV2,
        source_kind: PlanRevisionSourceKind,
        created_reason: PlanCreatedReason,
        generator_version: str,
    ) -> InterviewPlanRevision: ...

    def get_by_id(self, plan_revision_id: str) -> InterviewPlanRevision: ...

    def get_latest(self, plan_family_id: str) -> InterviewPlanRevision: ...

    def list_revisions(self, plan_family_id: str) -> list[InterviewPlanRevision]: ...


class InMemoryInterviewPlanRevisionStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._sources: dict[str, PlanSourceRecord] = {}
        self._source_by_family: dict[str, str] = {}
        self._source_refs: dict[tuple[str, str, str], PlanSourceReference] = {}
        self._revisions: dict[str, InterviewPlanRevision] = {}
        self._revision_ids_by_family: dict[str, list[str]] = {}

    def create_initial(
        self,
        *,
        source_payload: PlanSourcePayload,
        plan: InterviewPlanV2,
        retention_policy: str,
        generator_version: str,
        plan_family_id: str | None = None,
    ) -> InterviewPlanRevision:
        family_id = str(plan_family_id or uuid4())
        source_id = str(uuid4())
        revision_id = str(uuid4())
        now = utc_now()
        source = PlanSourceRecord(
            source_id=source_id,
            plan_family_id=family_id,
            source_sha256=source_payload_sha256(source_payload),
            protected_payload=source_payload,
            retention_policy=retention_policy,
            created_at=now,
        )
        revision = _build_revision(
            plan_revision_id=revision_id,
            plan_family_id=family_id,
            revision=1,
            parent_revision_id=None,
            source_kind="generated",
            source=source,
            plan=plan,
            generator_version=generator_version,
            created_reason="initial_generation",
            created_at=now,
        )
        with self._lock:
            if family_id in self._source_by_family:
                raise PlanRevisionConflict("plan family already exists", current_revision=1)
            self._sources[source_id] = source
            self._source_by_family[family_id] = source_id
            self._revisions[revision_id] = revision
            self._revision_ids_by_family[family_id] = [revision_id]
            self._source_refs[(source_id, "family", family_id)] = PlanSourceReference(
                source_id=source_id,
                owner_type="family",
                owner_id=family_id,
                created_at=now,
            )
        return _copy(revision)

    def create_next_revision(
        self,
        *,
        plan_family_id: str,
        expected_revision: int,
        plan: InterviewPlanV2,
        source_kind: PlanRevisionSourceKind,
        created_reason: PlanCreatedReason,
        generator_version: str,
    ) -> InterviewPlanRevision:
        with self._lock:
            current = self._latest_unlocked(plan_family_id)
            if current.revision != expected_revision:
                raise PlanRevisionConflict(
                    "expected revision does not match latest revision",
                    current_revision=current.revision,
                )
            source = self._sources[current.source_id]
            if source.protected_payload is None and created_reason in {
                "regenerate_question",
                "regenerate_all",
            }:
                raise PlanSourceUnavailable("plan source payload is unavailable")
            revision = _build_revision(
                plan_revision_id=str(uuid4()),
                plan_family_id=plan_family_id,
                revision=current.revision + 1,
                parent_revision_id=current.plan_revision_id,
                source_kind=source_kind,
                source=source,
                plan=plan,
                generator_version=generator_version,
                created_reason=created_reason,
                created_at=utc_now(),
            )
            self._revisions[revision.plan_revision_id] = revision
            self._revision_ids_by_family[plan_family_id].append(
                revision.plan_revision_id
            )
            return _copy(revision)

    def get_by_id(self, plan_revision_id: str) -> InterviewPlanRevision:
        with self._lock:
            try:
                return _copy(self._revisions[plan_revision_id])
            except KeyError as exc:
                raise PlanRevisionNotFound("plan revision not found") from exc

    def get_latest(self, plan_family_id: str) -> InterviewPlanRevision:
        with self._lock:
            return _copy(self._latest_unlocked(plan_family_id))

    def list_revisions(self, plan_family_id: str) -> list[InterviewPlanRevision]:
        with self._lock:
            ids = self._revision_ids_by_family.get(plan_family_id)
            if ids is None:
                raise PlanRevisionNotFound("plan family not found")
            return [_copy(self._revisions[revision_id]) for revision_id in ids]

    def get_source(self, source_id: str) -> PlanSourceRecord:
        with self._lock:
            try:
                return _copy(self._sources[source_id])
            except KeyError as exc:
                raise PlanRevisionNotFound("plan source not found") from exc

    def list_source_references(self, source_id: str) -> list[PlanSourceReference]:
        with self._lock:
            if source_id not in self._sources:
                raise PlanRevisionNotFound("plan source not found")
            refs = [
                ref
                for (ref_source_id, _, _), ref in self._source_refs.items()
                if ref_source_id == source_id
            ]
            return [_copy(ref) for ref in sorted(refs, key=lambda item: (item.owner_type, item.owner_id))]

    def add_source_reference(
        self,
        source_id: str,
        *,
        owner_type: PlanSourceReferenceType,
        owner_id: str,
    ) -> PlanSourceReference:
        with self._lock:
            source = self._sources.get(source_id)
            if source is None:
                raise PlanRevisionNotFound("plan source not found")
            key = (source_id, owner_type, owner_id)
            existing = self._source_refs.get(key)
            if existing is not None:
                return _copy(existing)
            ref = PlanSourceReference(
                source_id=source_id,
                owner_type=owner_type,
                owner_id=owner_id,
                created_at=utc_now(),
            )
            self._source_refs[key] = ref
            return _copy(ref)

    def remove_source_reference(
        self,
        source_id: str,
        *,
        owner_type: PlanSourceReferenceType,
        owner_id: str,
    ) -> bool:
        with self._lock:
            return self._source_refs.pop((source_id, owner_type, owner_id), None) is not None

    def tombstone_source_payload(self, source_id: str, *, reason: str) -> PlanSourceRecord:
        if not reason.strip():
            raise ValueError("tombstone reason is required")
        with self._lock:
            source = self._sources.get(source_id)
            if source is None:
                raise PlanRevisionNotFound("plan source not found")
            refs = [key for key in self._source_refs if key[0] == source_id]
            if refs:
                raise PlanSourceInUse("plan source still has active references")
            if source.protected_payload is None:
                return _copy(source)
            tombstoned = source.model_copy(
                update={
                    "protected_payload": None,
                    "tombstoned_at": utc_now(),
                    "tombstone_reason": reason.strip(),
                }
            )
            self._sources[source_id] = tombstoned
            return _copy(tombstoned)

    def _latest_unlocked(self, plan_family_id: str) -> InterviewPlanRevision:
        ids = self._revision_ids_by_family.get(plan_family_id)
        if not ids:
            raise PlanRevisionNotFound("plan family not found")
        return self._revisions[ids[-1]]


def _build_revision(
    *,
    plan_revision_id: str,
    plan_family_id: str,
    revision: int,
    parent_revision_id: str | None,
    source_kind: PlanRevisionSourceKind,
    source: PlanSourceRecord,
    plan: InterviewPlanV2,
    generator_version: str,
    created_reason: PlanCreatedReason,
    created_at,
) -> InterviewPlanRevision:
    return InterviewPlanRevision(
        plan_revision_id=plan_revision_id,
        plan_family_id=plan_family_id,
        revision=revision,
        parent_revision_id=parent_revision_id,
        source_kind=source_kind,
        source_id=source.source_id,
        source_sha256=source.source_sha256,
        configuration_snapshot=plan.configuration_snapshot,
        plan=plan,
        plan_sha256=plan_payload_sha256(plan),
        generator_version=generator_version,
        created_at=created_at,
        created_reason=created_reason,
    )


def _copy(value):
    return value.model_copy(deep=True) if hasattr(value, "model_copy") else deepcopy(value)
