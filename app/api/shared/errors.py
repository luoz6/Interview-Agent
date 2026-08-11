"""Shared translation from application errors to HTTP responses."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.domain.interview.errors import SessionDeletingError, SessionVersionConflict
from app.services.prep_plans import PrepPlanError


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
    "raise_prep_plan_error",
    "raise_session_deleting_error",
    "raise_value_error",
    "version_conflict_response",
]
