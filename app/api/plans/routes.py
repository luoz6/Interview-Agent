from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.shared import dependencies
from app.api.shared.projections import plan_revision_payload
from app.services.interview_plan_editor import (
    InterviewPlanEditor,
    PlanEditRequest,
    PlanOperation,
    PlanOperationValidationError,
)
from app.services.interview_plan_regenerator import (
    PlanRegenerationFailed,
    ProviderPlanRegenerator,
)
from app.services.interview_plan_revision import (
    PlanConfigurationSnapshot,
    canonical_sha256,
)
from app.services.interview_plan_revision_store import (
    PlanRevisionConflict,
    PlanRevisionNotFound,
    PlanSourceUnavailable,
)


router = APIRouter()

get_draft_store = dependencies.get_draft_store
get_plan_regenerator = dependencies.get_plan_regenerator
get_plan_revision_store = dependencies.get_plan_revision_store
get_prep_plan_store = dependencies.get_prep_plan_store
get_session_store = dependencies.get_session_store


class RegenerateQuestionRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1)


class RegenerateAllRequest(BaseModel):
    model_config = {"extra": "forbid"}

    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=1)
    confirmed: Literal[True]
    configuration: PlanConfigurationSnapshot | None = None


@router.patch("/interview-plans/{plan_family_id}")
def edit_interview_plan(
    plan_family_id: str,
    payload: PlanEditRequest,
    revision_store=Depends(dependencies.get_plan_revision_store),
):
    provider_managed = {
        operation.op
        for operation in payload.operations
        if operation.op in {"regenerate_question", "regenerate_all"}
    }
    if provider_managed:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "provider_managed_operation",
                "message": (
                    "regeneration output must be created by the server "
                    "Provider boundary"
                ),
                "operations": sorted(provider_managed),
            },
        )
    return _apply_plan_edit(plan_family_id, payload, revision_store)


@router.get("/interview-plans/{plan_family_id}/revisions")
def list_interview_plan_revisions(
    plan_family_id: str,
    revision_store=Depends(dependencies.get_plan_revision_store),
):
    try:
        revisions = revision_store.list_revisions(plan_family_id)
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    latest_revision = revisions[-1].revision
    return {
        "plan_family_id": plan_family_id,
        "latest_revision": latest_revision,
        "revisions": [
            {
                "plan_revision_id": revision.plan_revision_id,
                "revision": revision.revision,
                "parent_revision_id": revision.parent_revision_id,
                "plan_sha256": revision.plan_sha256,
                "created_at": revision.created_at.isoformat(),
                "created_reason": revision.created_reason,
                "source_kind": revision.source_kind,
                "title": revision.plan.title,
                "question_count": len(revision.plan.questions),
                "is_latest": revision.revision == latest_revision,
            }
            for revision in reversed(revisions)
        ],
    }


@router.get("/interview-plans/{plan_family_id}/revisions/{plan_revision_id}")
def get_interview_plan_revision(
    plan_family_id: str,
    plan_revision_id: str,
    revision_store=Depends(dependencies.get_plan_revision_store),
):
    try:
        revision = revision_store.get_by_id(plan_revision_id)
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if revision.plan_family_id != plan_family_id:
        raise HTTPException(status_code=404, detail="plan revision not found")
    return plan_revision_payload(revision)


def _apply_plan_edit(
    plan_family_id: str,
    payload: PlanEditRequest,
    revision_store,
    *,
    request_sha256: str | None = None,
    allow_configuration_change: bool = False,
):
    try:
        revision = InterviewPlanEditor(revision_store).apply(
            plan_family_id,
            payload,
            request_sha256=request_sha256,
            allow_configuration_change=allow_configuration_change,
        )
    except PlanRevisionConflict:
        current = None
        try:
            latest = revision_store.get_latest(plan_family_id)
            current = {
                "plan_revision_id": latest.plan_revision_id,
                "revision": latest.revision,
                "plan_sha256": latest.plan_sha256,
            }
        except PlanRevisionNotFound:
            pass
        return JSONResponse(
            status_code=409,
            content={
                "code": "plan_revision_conflict",
                "current_revision": current,
            },
        )
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanSourceUnavailable as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "plan_source_unavailable"},
        ) from exc
    except PlanOperationValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.detail()) from exc
    return plan_revision_payload(revision)


@router.post(
    "/interview-plans/{plan_family_id}/questions/{question_id}/regenerate"
)
def regenerate_interview_question(
    plan_family_id: str,
    question_id: str,
    payload: RegenerateQuestionRequest,
    revision_store=Depends(dependencies.get_plan_revision_store),
    regenerator: ProviderPlanRegenerator = Depends(
        dependencies.get_plan_regenerator
    ),
):
    request_sha256 = canonical_sha256(
        {
            "operation": "regenerate_question",
            "plan_family_id": plan_family_id,
            "question_id": question_id,
            **payload.model_dump(mode="json"),
        }
    )
    try:
        current = revision_store.get_latest(plan_family_id)
        if current.revision != payload.expected_revision:
            raise PlanRevisionConflict(
                "expected revision does not match latest revision",
                current_revision=current.revision,
            )
        source = revision_store.get_source(current.source_id)
        if source.protected_payload is None:
            raise PlanSourceUnavailable("plan source payload is unavailable")
        generated = regenerator.regenerate_question(
            current=current,
            source=source.protected_payload,
            question_id=question_id,
        )
    except PlanRevisionConflict:
        return _apply_plan_edit(
            plan_family_id,
            PlanEditRequest(
                expected_revision=payload.expected_revision,
                request_id=payload.request_id,
                operations=[
                    PlanOperation(
                        op="edit_focus",
                        question_id=question_id,
                        focus="idempotency-replay-placeholder",
                    )
                ],
            ),
            revision_store,
            request_sha256=request_sha256,
        )
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanSourceUnavailable as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "plan_source_unavailable"},
        ) from exc
    except PlanRegenerationFailed as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    request = PlanEditRequest(
        expected_revision=payload.expected_revision,
        request_id=payload.request_id,
        operations=[
            PlanOperation(
                op="regenerate_question",
                question_id=question_id,
                question_text=generated.question_text,
                focus=generated.focus,
                question_type=generated.question_type,
                difficulty=generated.difficulty,
                expected_minutes=generated.expected_minutes,
                expected_followups=generated.expected_followups,
                knowledge_binding=generated.knowledge_binding,
            )
        ],
    )
    return _apply_plan_edit(
        plan_family_id,
        request,
        revision_store,
        request_sha256=request_sha256,
    )

@router.post("/interview-plans/{plan_family_id}/regenerate")
def regenerate_entire_interview_plan(
    plan_family_id: str,
    payload: RegenerateAllRequest,
    revision_store=Depends(dependencies.get_plan_revision_store),
    regenerator: ProviderPlanRegenerator = Depends(
        dependencies.get_plan_regenerator
    ),
):
    try:
        current = revision_store.get_latest(plan_family_id)
        if current.revision != payload.expected_revision:
            return _apply_plan_edit(
                plan_family_id,
                PlanEditRequest(
                    expected_revision=payload.expected_revision,
                    request_id=payload.request_id,
                    operations=[
                        PlanOperation(
                            op="regenerate_all",
                            regenerated_plan=current.plan,
                        )
                    ],
                ),
                revision_store,
                request_sha256=canonical_sha256(
                    {
                        "operation": "regenerate_all",
                        "plan_family_id": plan_family_id,
                        **payload.model_dump(mode="json"),
                    }
                ),
            )
        source = revision_store.get_source(current.source_id)
        if source.protected_payload is None:
            raise PlanSourceUnavailable("plan source payload is unavailable")
        arguments = {"current": current, "source": source.protected_payload}
        if payload.configuration is not None:
            arguments["configuration"] = payload.configuration
        regenerated_plan = regenerator.regenerate_all(**arguments)
    except PlanRevisionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PlanSourceUnavailable as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "plan_source_unavailable"},
        ) from exc
    except PlanRegenerationFailed as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    request_sha256 = canonical_sha256(
        {
            "operation": "regenerate_all",
            "plan_family_id": plan_family_id,
            **payload.model_dump(mode="json"),
        }
    )
    return _apply_plan_edit(
        plan_family_id,
        PlanEditRequest(
            expected_revision=payload.expected_revision,
            request_id=payload.request_id,
            operations=[
                PlanOperation(
                    op="regenerate_all",
                    regenerated_plan=regenerated_plan,
                )
            ],
        ),
        revision_store,
        request_sha256=request_sha256,
        allow_configuration_change=payload.configuration is not None,
    )
