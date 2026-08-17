"""Shared translation from application errors to HTTP responses."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.domain.interview.errors import SessionDeletingError, SessionVersionConflict
from app.application.materials.service import UserMaterialsError
from app.services.interview_knowledge_scope import InterviewKnowledgeScopeError
from app.services.prep_plans import PrepPlanError


_USER_MATERIALS_ERRORS = {
    "document_not_found": (404, "未找到该资料。"),
    "unsupported_file_type": (422, "仅支持 UTF-8 Markdown 或 TXT 文件。"),
    "file_too_large": (422, "文件大小不能超过 1 MB。"),
    "invalid_utf8": (422, "文件必须使用 UTF-8 编码。"),
    "empty_document": (422, "文件内容不能为空。"),
    "retry_not_allowed": (409, "当前资料状态不允许重新处理。"),
    "document_deleted": (409, "该资料已被删除。"),
    "embedding_unavailable": (503, "资料处理服务暂时不可用。"),
    "index_write_failed": (503, "资料索引暂时无法写入。"),
    "processing_failed": (503, "资料处理暂时失败。"),
}

_KNOWLEDGE_SCOPE_ERRORS = {
    "knowledge_scope_duplicate_document": (
        422,
        "资料选择中包含重复项。",
    ),
    "knowledge_scope_document_not_found": (
        404,
        "未找到所选资料。",
    ),
    "knowledge_scope_document_unavailable": (
        409,
        "所选资料当前不可用，请返回准备页重新确认。",
    ),
}


def raise_if_deleting(state: dict) -> None:
    if state.get("deletion_status") == "deleting":
        raise_session_deleting_error(
            SessionDeletingError(str(state.get("session_id") or "unknown"))
        )


def raise_session_deleting_error(exc: SessionDeletingError) -> None:
    raise HTTPException(
        status_code=409,
        detail={"code": "session_deleting", "status": "deleting"},
    ) from exc


def raise_prep_plan_error(exc: PrepPlanError) -> None:
    raise exc


def raise_user_materials_error(exc: UserMaterialsError) -> None:
    status_code, message = _USER_MATERIALS_ERRORS.get(
        exc.code,
        (503, "资料服务暂时不可用。"),
    )
    code = exc.code if exc.code in _USER_MATERIALS_ERRORS else "processing_failed"
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    ) from exc


def raise_interview_knowledge_scope_error(
    exc: InterviewKnowledgeScopeError,
) -> None:
    status_code, message = _KNOWLEDGE_SCOPE_ERRORS.get(
        exc.code,
        (503, "资料范围暂时无法确认。"),
    )
    code = (
        exc.code
        if exc.code in _KNOWLEDGE_SCOPE_ERRORS
        else "knowledge_scope_unavailable"
    )
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    ) from exc


def raise_user_materials_hidden() -> None:
    raise HTTPException(
        status_code=404,
        detail={"code": "not_found", "message": "未找到资源。"},
    )


def raise_user_materials_invalid_request() -> None:
    raise HTTPException(
        status_code=422,
        detail={"code": "invalid_request", "message": "请求字段不合法。"},
    )


def raise_value_error(exc: ValueError) -> None:
    detail = str(exc)
    status_code = 404 if detail == "session not found" else 400
    raise HTTPException(status_code=status_code, detail=detail)


def version_conflict_response(exc: SessionVersionConflict) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": "session version conflict",
            "expected_version": exc.expected_version,
            "actual_version": exc.actual_version,
        },
    )


__all__ = [
    "raise_if_deleting",
    "raise_interview_knowledge_scope_error",
    "raise_prep_plan_error",
    "raise_session_deleting_error",
    "raise_user_materials_error",
    "raise_user_materials_hidden",
    "raise_user_materials_invalid_request",
    "raise_value_error",
    "version_conflict_response",
]
