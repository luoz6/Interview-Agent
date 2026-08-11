from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.application.interview.session_commands import (
    DurableSessionStream,
    InterviewApplicationService,
    LegacySessionStream,
    StreamingTurnService,
    publish_round_closed_event as _publish_round_closed_event,
    turn_to_dict as _turn_to_dict,
)
from app.application.interview.interview_start import InterviewStartService
from app.api.shared import dependencies
from app.api.shared.dependencies import get_draft_store, get_prep_plan_store
from app.api.shared.errors import raise_if_deleting as _raise_if_deleting
from app.api.shared.errors import raise_prep_plan_error as _raise_prep_plan_error
from app.api.shared.errors import (
    raise_session_deleting_error as _raise_session_deleting_error,
)
from app.api.shared.errors import raise_value_error as _raise_value_error
from app.api.shared.errors import version_conflict_response as _version_conflict_response
from app.api.shared.models import (
    AnswerRequest,
    DraftRequest,
    SessionCommandRequest,
    StartInterviewRequest,
)
from app.domain.interview.commands import SessionCommand
from app.domain.interview.errors import SessionDeletingError, SessionVersionConflict
from app.services.agent_runtime import correlation_id_from_plan
from app.services.interview_launch import InterviewLaunchCoordinator
from app.services.job_tags import extract_job_tags
from app.ports.runtime import InterviewSessionRepository
from app.services.prep_plans import PrepPlanError


router = APIRouter()


@router.post("/interview-drafts")
def save_interview_draft(payload: DraftRequest, draft_store=Depends(get_draft_store)):
    try:
        return draft_store.save(
            draft_id=payload.draft_id,
            job_description=payload.job_description,
            resume_text=payload.resume_text,
            title=payload.title,
            job_tags=(
                payload.job_tags
                if payload.job_tags is not None
                else extract_job_tags(payload.job_description)
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/interview-drafts/{draft_id}")
def get_interview_draft(draft_id: str, draft_store=Depends(get_draft_store)):
    try:
        return draft_store.get(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/interview-drafts/{draft_id}", status_code=204)
def delete_interview_draft(
    draft_id: str,
    draft_store=Depends(get_draft_store),
    plan_store=Depends(get_prep_plan_store),
):
    if not draft_store.delete(draft_id):
        raise HTTPException(status_code=404, detail="draft not found")
    if getattr(draft_store, "durability", None) != "postgres":
        plan_store.delete_by_source_draft(draft_id)
    return Response(status_code=204)


# Compatibility dependency exports. Runtime calls below resolve through the
# shared module so replacements are observed without import-time store capture.
get_agent_execution_runner = dependencies.get_agent_execution_runner
get_event_publisher = dependencies.get_event_publisher
get_interview_launch_coordinator = dependencies.get_interview_launch_coordinator
get_interview_workflow_service = dependencies.get_interview_workflow_service
get_report_job_store = dependencies.get_report_job_store
get_runtime_control_store = dependencies.get_runtime_control_store
get_session_store = dependencies.get_session_store
get_legacy_launch_session_store = dependencies.get_legacy_launch_session_store
get_request_interview_launch_coordinator = (
    dependencies.get_request_interview_launch_coordinator
)
@router.post("/interviews")
def start_interview(
    payload: StartInterviewRequest,
    start_service: InterviewStartService | None = Depends(
        dependencies.get_legacy_interview_start_service
    ),
    launch_coordinator: InterviewLaunchCoordinator | None = Depends(
        get_request_interview_launch_coordinator
    ),
):
    if payload.plan_id is not None:
        if payload.expected_plan_version is None or not payload.command_id:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_LAUNCH_REQUEST",
                    "message": "缺少计划版本或安全启动标识。",
                    "retryable": False,
                },
            )
        if launch_coordinator is None:
            raise HTTPException(status_code=503, detail="launch coordinator is unavailable")
        try:
            return launch_coordinator.launch(
                plan_id=payload.plan_id,
                expected_plan_version=payload.expected_plan_version,
                command_id=payload.command_id,
            )
        except PrepPlanError as exc:
            _raise_prep_plan_error(exc)

    if not payload.job_description or not payload.resume_text:
        raise HTTPException(status_code=422, detail="legacy launch input is incomplete")
    if start_service is None:
        raise HTTPException(status_code=503, detail="interview start is unavailable")
    try:
        turn = start_service.start(
            job_description=payload.job_description,
            resume_text=payload.resume_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _turn_to_dict(turn)


@router.get("/interviews/{session_id}")
def get_interview_session(
    session_id: str,
    application: InterviewApplicationService = Depends(
        dependencies.get_interview_application_service
    ),
):
    try:
        return application.snapshot(session_id)
    except SessionDeletingError as exc:
        _raise_session_deleting_error(exc)
    except ValueError as exc:
        _raise_value_error(exc)


@router.get("/interviews/{session_id}/agent-runs")
def list_agent_runs(
    session_id: str,
    agent: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    store: InterviewSessionRepository = Depends(dependencies.get_session_store),
    control=Depends(dependencies.get_runtime_control_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        _raise_value_error(exc)
    if control is None:
        return {"session_id": session_id, "items": []}
    correlation_id = correlation_id_from_plan(
        state["plan"],
        session_id=session_id,
    )
    return {
        "session_id": session_id,
        "items": control.list_agent_runs(
            session_id=session_id,
            correlation_id=correlation_id,
            agent=agent,
            status=status,
            limit=limit,
        ),
    }


@router.get("/interviews/{session_id}/runtime-events")
def list_runtime_events(
    session_id: str,
    status: str | None = None,
    event_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    store: InterviewSessionRepository = Depends(dependencies.get_session_store),
    control=Depends(dependencies.get_runtime_control_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        _raise_value_error(exc)
    if control is None:
        return {"session_id": session_id, "items": []}
    return {
        "session_id": session_id,
        "items": control.list_runtime_events(
            session_id=session_id,
            status=status,
            event_type=event_type,
            limit=limit,
        ),
    }


@router.post("/interviews/{session_id}/answer")
def submit_answer(
    session_id: str,
    payload: AnswerRequest,
    application: InterviewApplicationService = Depends(
        dependencies.get_interview_application_service
    ),
):
    try:
        result = application.execute(
            SessionCommand.answer(
                session_id,
                payload.answer,
                expected_version=payload.expected_version,
                command_id=payload.command_id,
            )
        )
        if result.accepted is not None:
            return JSONResponse(
                status_code=202,
                content=result.accepted.model_dump(mode="json"),
            )
        if result.turn is None:
            raise RuntimeError("legacy session command did not return a turn")
        return _turn_to_dict(result.turn)
    except SessionDeletingError as exc:
        _raise_session_deleting_error(exc)
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)


@router.post("/interviews/{session_id}/answer/stream")
def submit_answer_stream(
    session_id: str,
    payload: AnswerRequest,
    streaming: StreamingTurnService = Depends(
        dependencies.get_streaming_turn_service
    ),
):
    try:
        result = streaming.prepare(
            SessionCommand.answer(
                session_id,
                payload.answer,
                expected_version=payload.expected_version,
                command_id=payload.command_id,
            )
        )
        if isinstance(result, DurableSessionStream):
            return StreamingResponse(
                result.events,
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
    except SessionDeletingError as exc:
        _raise_session_deleting_error(exc)
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)

    if not isinstance(result, LegacySessionStream):
        raise RuntimeError("unknown interview stream result")

    def event_stream():
        for event in result.events:
            yield event.to_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/interviews/{session_id}/finish")
def finish_interview(
    session_id: str,
    payload: SessionCommandRequest | None = None,
    application: InterviewApplicationService = Depends(
        dependencies.get_interview_application_service
    ),
):
    payload = payload or SessionCommandRequest()
    try:
        result = application.execute(
            SessionCommand(
                session_id=session_id,
                command_type="finish",
                expected_version=payload.expected_version,
                command_id=payload.command_id,
            )
        )
        if result.accepted is not None:
            return JSONResponse(
                status_code=202,
                content=result.accepted.model_dump(mode="json"),
            )
        if result.turn is None:
            raise RuntimeError("legacy session command did not return a turn")
        return _turn_to_dict(result.turn)
    except SessionDeletingError as exc:
        _raise_session_deleting_error(exc)
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)


@router.post("/interviews/{session_id}/skip")
def skip_interview_question(
    session_id: str,
    payload: SessionCommandRequest | None = None,
    application: InterviewApplicationService = Depends(
        dependencies.get_interview_application_service
    ),
):
    payload = payload or SessionCommandRequest()
    try:
        result = application.execute(
            SessionCommand(
                session_id=session_id,
                command_type="skip",
                expected_version=payload.expected_version,
                command_id=payload.command_id,
            )
        )
        if result.accepted is not None:
            return JSONResponse(
                status_code=202,
                content=result.accepted.model_dump(mode="json"),
            )
        if result.turn is None:
            raise RuntimeError("legacy session command did not return a turn")
        return _turn_to_dict(result.turn)
    except SessionDeletingError as exc:
        _raise_session_deleting_error(exc)
    except SessionVersionConflict as exc:
        return _version_conflict_response(exc)
    except ValueError as exc:
        _raise_value_error(exc)


@router.get(
    "/interviews/{session_id}/commands/{command_id}/stream"
)
def stream_interview_command(
    session_id: str,
    command_id: str,
    request: Request,
    store: InterviewSessionRepository = Depends(dependencies.get_session_store),
):
    try:
        state = store.get(session_id)
        _raise_if_deleting(state)
    except ValueError as exc:
        _raise_value_error(exc)
    workflow = dependencies.get_interview_workflow_service()
    return StreamingResponse(
        workflow.event_stream.iter_sse(
            session_id,
            command_id,
            after_event_id=request.headers.get("Last-Event-ID"),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
__all__ = [
    "_publish_round_closed_event",
    "delete_interview_draft",
    "finish_interview",
    "get_interview_draft",
    "get_interview_session",
    "get_legacy_launch_session_store",
    "get_request_interview_launch_coordinator",
    "list_agent_runs",
    "list_runtime_events",
    "router",
    "save_interview_draft",
    "skip_interview_question",
    "start_interview",
    "stream_interview_command",
    "submit_answer",
    "submit_answer_stream",
]
