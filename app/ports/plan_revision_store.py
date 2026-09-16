from __future__ import annotations

from typing import Mapping, Protocol

from app.domain.interview.plan_audit import (
    PlanAuditFieldDiff,
    PlanAuditOperation,
    PlanRevisionAudit,
)
from app.domain.interview.plan_revision import (
    InterviewPlanRevision,
    InterviewPlanRevisionPayload,
    PlanConfigurationSnapshot,
    PlanCreatedReason,
    PlanRevisionSourceKind,
    PlanSourcePayload,
    PlanSourceRecord,
    PlanSourceReference,
    PlanSourceReferenceType,
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
        plan: InterviewPlanRevisionPayload,
        retention_policy: str,
        generator_version: str,
        plan_family_id: str | None = None,
    ) -> InterviewPlanRevision: ...

    def create_next_revision(
        self,
        *,
        plan_family_id: str,
        expected_revision: int,
        plan: InterviewPlanRevisionPayload,
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
