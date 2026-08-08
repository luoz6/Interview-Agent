from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from threading import RLock
from typing import Mapping, Protocol
from uuid import uuid4

from app.services.interview_plan_audit import (
    PlanAuditFieldDiff,
    PlanAuditOperation,
    PlanRevisionAudit,
)
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
    def source_reference_recovery_lock(self): ...

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
        request_id: str | None = None,
        request_sha256: str | None = None,
        audit: PlanRevisionAudit | None = None,
    ) -> InterviewPlanRevision: ...

    def get_by_id(self, plan_revision_id: str) -> InterviewPlanRevision: ...

    def get_latest(self, plan_family_id: str) -> InterviewPlanRevision: ...

    def list_revisions(self, plan_family_id: str) -> list[InterviewPlanRevision]: ...

    def get_source(self, source_id: str) -> PlanSourceRecord: ...

    def list_source_references(
        self, source_id: str
    ) -> list[PlanSourceReference]: ...

    def add_source_reference(
        self,
        source_id: str,
        *,
        owner_type: PlanSourceReferenceType,
        owner_id: str,
    ) -> PlanSourceReference: ...

    def replace_source_reference(
        self,
        *,
        old_source_id: str | None,
        new_source_id: str | None,
        owner_type: PlanSourceReferenceType,
        owner_id: str,
    ) -> PlanSourceReference | None: ...

    def remove_source_reference(
        self,
        source_id: str,
        *,
        owner_type: PlanSourceReferenceType,
        owner_id: str,
    ) -> bool: ...

    def reconcile_source_references(
        self,
        *,
        owner_type: PlanSourceReferenceType,
        expected: Mapping[str, str],
    ) -> int: ...

    def reconcile_session_source_references(self) -> int: ...

    def tombstone_source_payload(
        self, source_id: str, *, reason: str
    ) -> PlanSourceRecord: ...


class InMemoryInterviewPlanRevisionStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._sources: dict[str, PlanSourceRecord] = {}
        self._source_by_family: dict[str, str] = {}
        self._source_refs: dict[tuple[str, str, str], PlanSourceReference] = {}
        self._revisions: dict[str, InterviewPlanRevision] = {}
        self._revision_ids_by_family: dict[str, list[str]] = {}
        self._requests: dict[tuple[str, str], tuple[str, str]] = {}

    @contextmanager
    def source_reference_recovery_lock(self):
        with self._lock:
            yield

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
        request_id: str | None = None,
        request_sha256: str | None = None,
        audit: PlanRevisionAudit | None = None,
    ) -> InterviewPlanRevision:
        _validate_request_identity(request_id, request_sha256)
        with self._lock:
            if request_id is not None:
                stored = self._requests.get((plan_family_id, request_id))
                if stored is not None:
                    if stored[0] != request_sha256:
                        raise PlanRevisionConflict("request ID payload conflicts")
                    return _copy(self._revisions[stored[1]])
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
            if audit is not None and audit.parent_plan_sha256 != current.plan_sha256:
                raise ValueError("revision audit parent hash does not match current revision")
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
                audit=(
                    audit
                    or _default_revision_audit(
                        created_reason=created_reason,
                        source_sha256=source.source_sha256,
                        parent_plan_sha256=current.plan_sha256,
                        result_plan_sha256=plan_payload_sha256(plan),
                    )
                ),
            )
            self._revisions[revision.plan_revision_id] = revision
            self._revision_ids_by_family[plan_family_id].append(
                revision.plan_revision_id
            )
            if request_id is not None:
                self._requests[(plan_family_id, request_id)] = (
                    request_sha256 or "",
                    revision.plan_revision_id,
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
            if source.protected_payload is None:
                raise PlanSourceUnavailable("plan source payload is unavailable")
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

    def replace_source_reference(
        self,
        *,
        old_source_id: str | None,
        new_source_id: str | None,
        owner_type: PlanSourceReferenceType,
        owner_id: str,
    ) -> PlanSourceReference | None:
        if old_source_id is None and new_source_id is None:
            return None
        with self._lock:
            if new_source_id is not None:
                source = self._sources.get(new_source_id)
                if source is None:
                    raise PlanRevisionNotFound("plan source not found")
                if source.protected_payload is None:
                    raise PlanSourceUnavailable(
                        "plan source payload is unavailable"
                    )
            if old_source_id is not None and old_source_id != new_source_id:
                self._source_refs.pop(
                    (old_source_id, owner_type, owner_id), None
                )
            if new_source_id is None:
                return None
            key = (new_source_id, owner_type, owner_id)
            existing = self._source_refs.get(key)
            if existing is not None:
                return _copy(existing)
            ref = PlanSourceReference(
                source_id=new_source_id,
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

    def reconcile_source_references(
        self,
        *,
        owner_type: PlanSourceReferenceType,
        expected: Mapping[str, str],
    ) -> int:
        with self._lock:
            for source_id in expected.values():
                source = self._sources.get(source_id)
                if source is None:
                    raise PlanRevisionNotFound("plan source not found")
                if source.protected_payload is None:
                    raise PlanSourceUnavailable("plan source payload is unavailable")
            changed = 0
            expected_pairs = {(source_id, owner_id) for owner_id, source_id in expected.items()}
            expected_owner_ids = set(expected)
            for key in list(self._source_refs):
                source_id, ref_owner_type, owner_id = key
                if (
                    ref_owner_type == owner_type
                    and owner_id in expected_owner_ids
                    and (source_id, owner_id) not in expected_pairs
                ):
                    del self._source_refs[key]
                    changed += 1
            for owner_id, source_id in expected.items():
                key = (source_id, owner_type, owner_id)
                if key not in self._source_refs:
                    self._source_refs[key] = PlanSourceReference(
                        source_id=source_id,
                        owner_type=owner_type,
                        owner_id=owner_id,
                        created_at=utc_now(),
                    )
                    changed += 1
            return changed

    def reconcile_session_source_references(self) -> int:
        # Memory sessions and references share the process lifetime. Persistent
        # session recovery is implemented by the PostgreSQL store.
        return 0

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
    audit: PlanRevisionAudit | None = None,
) -> InterviewPlanRevision:
    result_plan_sha256 = plan_payload_sha256(plan)
    effective_audit = audit or _default_revision_audit(
        created_reason=created_reason,
        source_sha256=source.source_sha256,
        parent_plan_sha256=None if revision == 1 else None,
        result_plan_sha256=result_plan_sha256,
    )
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
        plan_sha256=result_plan_sha256,
        generator_version=generator_version,
        created_at=created_at,
        created_reason=created_reason,
        audit=effective_audit,
    )


def _default_revision_audit(
    *,
    created_reason: PlanCreatedReason,
    source_sha256: str,
    parent_plan_sha256: str | None,
    result_plan_sha256: str,
) -> PlanRevisionAudit:
    changed = parent_plan_sha256 != result_plan_sha256
    actor = (
        "provider"
        if created_reason in {"regenerate_question", "regenerate_all"}
        else "system"
    )
    return PlanRevisionAudit(
        created_reason=created_reason,
        source_sha256=source_sha256,
        parent_plan_sha256=parent_plan_sha256,
        result_plan_sha256=result_plan_sha256,
        configuration_diff={},
        operations=(
            PlanAuditOperation(
                operation=created_reason,
                actor=actor,
                reason_code=(
                    "initial_generation"
                    if created_reason == "initial_generation"
                    else "direct_store_write"
                ),
                changed_fields=("plan",) if changed else (),
                field_diffs=(
                    {
                        "plan": PlanAuditFieldDiff(
                            before_sha256=parent_plan_sha256,
                            after_sha256=result_plan_sha256,
                        )
                    }
                    if changed
                    else {}
                ),
                knowledge_binding_action=(
                    "build"
                    if created_reason == "initial_generation"
                    else (
                        "rebuild"
                        if created_reason == "regenerate_question"
                        else (
                            "rebuild_all"
                            if created_reason == "regenerate_all"
                            else "none"
                        )
                    )
                ),
            ),
        ),
    )


def _copy(value):
    return value.model_copy(deep=True) if hasattr(value, "model_copy") else deepcopy(value)


def _validate_request_identity(
    request_id: str | None, request_sha256: str | None
) -> None:
    if (request_id is None) != (request_sha256 is None):
        raise ValueError("request_id and request_sha256 must be supplied together")
    if request_id is not None and not request_id.strip():
        raise ValueError("request_id must not be blank")
    if request_sha256 is not None and (
        len(request_sha256) != 64
        or any(char not in "0123456789abcdef" for char in request_sha256)
    ):
        raise ValueError("request_sha256 must be lowercase SHA-256")
