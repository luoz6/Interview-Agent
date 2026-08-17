import inspect
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.api.shared.dependencies import (
    get_agent_execution_runner,
    get_prep_knowledge_repository,
    get_plan_revision_store,
    get_prep_plan_store,
    get_prep_question_regenerator,
    get_principal_identity_resolver,
    get_request_interview_knowledge_scope_resolver,
    get_user_materials_runtime_settings,
)
from app.api.shared.errors import (
    raise_interview_knowledge_scope_error,
    raise_prep_plan_error,
    raise_user_materials_hidden,
)
from app.api.shared.models import (
    PrepPlanPatchRequest,
    PrepQuestionRegenerateRequest,
    PrepRequest,
)
from app.api.shared.projections import plan_revision_payload
from app.domain.knowledge.source_scope import build_knowledge_source_scope
from app.services.job_tags import extract_job_tags
from app.services.interview_knowledge_scope import InterviewKnowledgeScopeError
from app.services.interview_plan_revision import (
    build_interview_knowledge_scope_snapshot,
    default_plan_configuration,
    legacy_interview_knowledge_scope_snapshot,
)
from app.services.prep import (
    PlanGenerationValidationError,
    prepare_interview,
    prepared_plan_revision,
)
from app.services.prep_plans import PrepPlanError
from app.services.prep_question_regeneration import PrepQuestionRegenerator


router = APIRouter()


@router.post("/prep")
def prep_interview(
    payload: PrepRequest,
    plan_store=Depends(get_prep_plan_store),
    revision_store=Depends(get_plan_revision_store),
    knowledge_store=Depends(get_prep_knowledge_repository),
    scope_resolver=Depends(get_request_interview_knowledge_scope_resolver),
    principal_resolver=Depends(get_principal_identity_resolver),
    materials_settings=Depends(get_user_materials_runtime_settings),
):
    configuration = payload.configuration or default_plan_configuration()
    knowledge_source_scope = None
    source_owner_principal_id = None
    if payload.knowledge_scope is None:
        knowledge_scope = legacy_interview_knowledge_scope_snapshot()
    else:
        selected_document_ids = payload.knowledge_scope.selected_document_ids
        if (
            selected_document_ids
            and not materials_settings.enabled
        ):
            raise_user_materials_hidden()
        identity = principal_resolver.resolve()
        if identity is None:
            raise_user_materials_hidden()
        source_owner_principal_id = identity.principal_id
        if scope_resolver is None:
            if selected_document_ids:
                raise_user_materials_hidden()
            knowledge_scope = build_interview_knowledge_scope_snapshot(
                include_system_knowledge=(
                    payload.knowledge_scope.include_system_knowledge
                ),
                selected_documents=(),
                created_at=datetime.now(timezone.utc),
            )
        else:
            try:
                knowledge_scope = scope_resolver.resolve(
                    owner_principal_id=identity.principal_id,
                    selected_document_ids=selected_document_ids,
                    include_system_knowledge=(
                        payload.knowledge_scope.include_system_knowledge
                    ),
                )
            except InterviewKnowledgeScopeError as exc:
                raise_interview_knowledge_scope_error(exc)
        knowledge_source_scope = build_knowledge_source_scope(
            knowledge_scope,
            owner_principal_id=identity.principal_id,
            usage="question",
        )
    try:
        kwargs = {
            "execution_runner": get_agent_execution_runner(),
            "configuration": configuration,
        }
        if "knowledge_store" in inspect.signature(prepare_interview).parameters:
            kwargs["knowledge_store"] = knowledge_store
        if "knowledge_scope" in inspect.signature(prepare_interview).parameters:
            kwargs["knowledge_scope"] = knowledge_scope
        if "knowledge_source_scope" in inspect.signature(
            prepare_interview
        ).parameters:
            kwargs["knowledge_source_scope"] = knowledge_source_scope
        plan = prepare_interview(
            payload.job_description,
            payload.resume_text,
            **kwargs,
        )
        revision_plan = prepared_plan_revision(
            plan,
            configuration,
            knowledge_scope=knowledge_scope,
        )
    except PlanGenerationValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    job_tags = extract_job_tags(payload.job_description)
    try:
        response = plan_store.create(
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
    revision = revision_store.create_initial(
        source_payload={
            "job_description": payload.job_description,
            "resume_text": payload.resume_text,
            "job_tags": job_tags,
            "owner_principal_id": source_owner_principal_id,
        },
        plan=revision_plan,
        retention_policy="local-v1",
        generator_version=configuration.generator_version,
    )
    response.update(plan_revision_payload(revision))
    response["job_tags"] = job_tags
    return response


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
