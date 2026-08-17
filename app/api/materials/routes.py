from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status

from app.api.materials.models import (
    MaterialDeleteResponse,
    MaterialPatchRequest,
    MaterialResponse,
    MaterialsListResponse,
)
from app.api.shared.dependencies import (
    get_principal_identity_resolver,
    get_user_document_deletion_service,
    get_user_document_ingestion_service,
    get_user_document_service,
    get_user_materials_runtime_settings,
)
from app.api.shared.errors import (
    raise_user_materials_error,
    raise_user_materials_hidden,
    raise_user_materials_invalid_request,
)
from app.application.materials.deletion_service import UserDocumentDeletionService
from app.application.materials.ingestion_service import UserDocumentIngestionService
from app.application.materials.service import UserDocumentService, UserMaterialsError
from app.domain.knowledge.user_document import USER_DOCUMENT_MAX_BYTES
from app.runtime.config.models import UserMaterialsRuntimeSettings


router = APIRouter(prefix="/materials", tags=["materials"])


@router.get("", response_model=MaterialsListResponse)
def list_materials(
    settings: UserMaterialsRuntimeSettings = Depends(
        get_user_materials_runtime_settings
    ),
    resolver=Depends(get_principal_identity_resolver),
    service: UserDocumentService = Depends(get_user_document_service),
) -> MaterialsListResponse:
    _require_capability(settings)
    owner_principal_id = _principal_id(resolver)
    documents = service.list_documents(owner_principal_id=owner_principal_id)
    return MaterialsListResponse(
        items=tuple(MaterialResponse.from_document(item) for item in documents)
    )


@router.post(
    "",
    response_model=MaterialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_material(
    request: Request,
    file: Annotated[UploadFile, File(...)],
    display_name: Annotated[
        str | None,
        Form(min_length=1, max_length=200),
    ] = None,
    settings: UserMaterialsRuntimeSettings = Depends(
        get_user_materials_runtime_settings
    ),
    resolver=Depends(get_principal_identity_resolver),
    service: UserDocumentIngestionService = Depends(
        get_user_document_ingestion_service
    ),
) -> MaterialResponse:
    _require_capability(settings, ingest=True)
    owner_principal_id = _principal_id(resolver)
    filename = file.filename or ""
    media_type = file.content_type or ""
    try:
        form = await request.form()
        if set(form) - {"file", "display_name"}:
            raise_user_materials_invalid_request()
        if display_name is not None:
            normalized = " ".join(display_name.strip().split())
            if not normalized or any(
                ord(character) < 32 for character in normalized
            ):
                raise_user_materials_invalid_request()
            display_name = normalized
        content = await file.read(USER_DOCUMENT_MAX_BYTES + 1)
    finally:
        await file.close()
    try:
        document = service.ingest(
            owner_principal_id=owner_principal_id,
            original_filename=filename,
            media_type=media_type,
            content=content,
            display_title=display_name,
        )
    except UserMaterialsError as exc:
        raise_user_materials_error(exc)
    return MaterialResponse.from_document(document)


@router.patch("/{document_id}", response_model=MaterialResponse)
def patch_material(
    document_id: str,
    payload: MaterialPatchRequest,
    settings: UserMaterialsRuntimeSettings = Depends(
        get_user_materials_runtime_settings
    ),
    resolver=Depends(get_principal_identity_resolver),
    service: UserDocumentService = Depends(get_user_document_service),
) -> MaterialResponse:
    _require_capability(settings)
    owner_principal_id = _principal_id(resolver)
    try:
        document = service.patch_document(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
            display_title=payload.display_name,
            enabled=payload.enabled,
            allowed_usages=payload.allowed_usage,
        )
    except UserMaterialsError as exc:
        raise_user_materials_error(exc)
    return MaterialResponse.from_document(document)


@router.post("/{document_id}/retry", response_model=MaterialResponse)
def retry_material(
    document_id: str,
    settings: UserMaterialsRuntimeSettings = Depends(
        get_user_materials_runtime_settings
    ),
    resolver=Depends(get_principal_identity_resolver),
    service: UserDocumentIngestionService = Depends(
        get_user_document_ingestion_service
    ),
) -> MaterialResponse:
    _require_capability(settings, ingest=True)
    owner_principal_id = _principal_id(resolver)
    try:
        document = service.retry(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
        )
    except UserMaterialsError as exc:
        raise_user_materials_error(exc)
    return MaterialResponse.from_document(document)


@router.delete("/{document_id}", response_model=MaterialDeleteResponse)
def delete_material(
    document_id: str,
    resolver=Depends(get_principal_identity_resolver),
    service: UserDocumentDeletionService = Depends(
        get_user_document_deletion_service
    ),
) -> MaterialDeleteResponse:
    owner_principal_id = _principal_id(resolver)
    try:
        result = service.delete(
            owner_principal_id=owner_principal_id,
            document_id=document_id,
        )
    except UserMaterialsError as exc:
        raise_user_materials_error(exc)
    return MaterialDeleteResponse(document_id=result.document_id)


def _require_capability(
    settings: UserMaterialsRuntimeSettings,
    *,
    ingest: bool = False,
) -> None:
    if not settings.enabled or (ingest and not settings.ingest_enabled):
        raise_user_materials_hidden()


def _principal_id(resolver) -> str:
    identity = resolver.resolve()
    if identity is None:
        raise_user_materials_hidden()
    return identity.principal_id


__all__ = ["router"]
