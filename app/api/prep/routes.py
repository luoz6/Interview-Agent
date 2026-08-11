from fastapi import APIRouter, Depends, HTTPException

from app.api.shared.dependencies import (
    get_agent_execution_runner,
    get_prep_plan_store,
    get_prep_question_regenerator,
)
from app.api.shared.errors import raise_prep_plan_error
from app.api.shared.models import (
    PrepPlanPatchRequest,
    PrepQuestionRegenerateRequest,
    PrepRequest,
)
from app.services.job_tags import extract_job_tags
from app.services.prep import prepare_interview
from app.services.prep_plans import PrepPlanError
from app.services.prep_question_regeneration import PrepQuestionRegenerator


router = APIRouter()


@router.post("/prep")
def prep_interview(payload: PrepRequest, plan_store=Depends(get_prep_plan_store)):
    try:
        plan = prepare_interview(
            payload.job_description,
            payload.resume_text,
            execution_runner=get_agent_execution_runner(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    job_tags = extract_job_tags(payload.job_description)
    try:
        return plan_store.create(
            plan=plan,
            job_description=payload.job_description,
            resume_text=payload.resume_text,
            job_tags=job_tags,
            source_draft_id=payload.draft_id,
        )
    except PrepPlanError as exc:
        raise_prep_plan_error(exc)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PREP_PLAN_STORE_UNAVAILABLE",
                "message": "计划暂时无法保存，请稍后重试。",
                "retryable": True,
            },
        ) from exc


@router.get("/prep-plans/{plan_id}")
def get_prep_plan(plan_id: str, plan_store=Depends(get_prep_plan_store)):
    try:
        return plan_store.get(plan_id)
    except PrepPlanError as exc:
        raise_prep_plan_error(exc)


@router.patch("/prep-plans/{plan_id}")
def patch_prep_plan(
    plan_id: str,
    payload: PrepPlanPatchRequest,
    plan_store=Depends(get_prep_plan_store),
):
    try:
        return plan_store.apply_operations(
            plan_id,
            expected_version=payload.expected_version,
            operations=payload.operations,
        )
    except PrepPlanError as exc:
        raise_prep_plan_error(exc)


@router.post("/prep-plans/{plan_id}/questions/{question_id}/regenerate")
def regenerate_prep_question(
    plan_id: str,
    question_id: str,
    payload: PrepQuestionRegenerateRequest,
    plan_store=Depends(get_prep_plan_store),
    regenerator: PrepQuestionRegenerator = Depends(get_prep_question_regenerator),
):
    try:
        return regenerator.regenerate(
            plan_store,
            plan_id=plan_id,
            question_id=question_id,
            expected_version=payload.expected_version,
        )
    except PrepPlanError as exc:
        raise_prep_plan_error(exc)


__all__ = [
    "get_prep_plan",
    "patch_prep_plan",
    "prep_interview",
    "regenerate_prep_question",
    "router",
]
